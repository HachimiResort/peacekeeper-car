#include <stdio.h>
#include <string.h>
#include "cmsis_os2.h"
#include "los_interrupt.h"
#include "los_task.h"
#include "ohos_init.h"
#include "at32f403a_407.h"
#include "cJSON.h"  //cJson解析三方库
#include "usart1.h" //usart1串口通信的部分

// pid和编码器控制的部分
#include "bsp_motor.h"
#include "bsp_encoder.h"
#include "bsp_beep.h"
#include "bsp_tracing.h"
#include "protocol.h"
//
#include "app_motion.h"
#include "app_pid.h"
#include "app_mecanum.h"
// 灯
#include "bsp_car_lights.h"

/********************************************************************************************************/
/* UART ISR uses RxBuffer only to assemble the frame currently on the wire. */
static uint8_t RxBuffer[PTO_MAX_BUF_LEN];
static uint8_t RxIndex = 0;
static uint8_t RxFlag = 0;
static uint8_t RxLength = 0;

/*
 * Discrete commands retain their order in this small FIFO. Motion commands
 * are intentionally kept out of the FIFO: only their newest value matters.
 */
#define PTO_EVENT_QUEUE_DEPTH 4U
#define PTO_EVENT_QUEUE_MASK  (PTO_EVENT_QUEUE_DEPTH - 1U)

typedef struct {
    uint8_t length;
    uint8_t data[PTO_MAX_BUF_LEN];
} ProtocolFrame;

static ProtocolFrame EventQueue[PTO_EVENT_QUEUE_DEPTH];
static volatile uint8_t EventQueueWrite = 0;
static volatile uint8_t EventQueueRead = 0;
static volatile uint32_t EventQueueOverflow = 0;
static volatile uint32_t RxChecksumError = 0;

static ProtocolFrame LatestMotionFrame;
static volatile uint8_t LatestMotionValid = 0;

/* ActiveFrame is owned exclusively by vTask_Control. */
static ProtocolFrame ActiveFrame;
static uint8_t ActiveFrameValid = 0;
/********************************************************************************************************/
static uint8_t Protocol_IsMotionCommand(uint8_t func_id)
{
    return (func_id == FUNC_MOTOR) || (func_id == FUNC_CAR_RUN) ||
           (func_id == FUNC_MOTION);
}

static uint8_t Protocol_IsChecksumValid(const uint8_t *data, uint8_t length)
{
    uint8_t sum = 0;
    for (uint8_t i = 2; i < (length - 1U); i++)
    {
        sum += data[i];
    }
    return sum == data[length - 1U];
}

static void Protocol_ResetRxState(void)
{
    RxIndex = 0;
    RxFlag = 0;
    RxLength = 0;
    RxBuffer[0] = 0;
    RxBuffer[1] = 0;
}

/* Called only by USART1_IRQHandler after a complete frame has arrived. */
static void Protocol_StoreFrame(const uint8_t *data, uint8_t length)
{
    if (!Protocol_IsChecksumValid(data, length))
    {
        RxChecksumError++;
        return;
    }

    if (Protocol_IsMotionCommand(data[3]))
    {
        memcpy(LatestMotionFrame.data, data, length);
        LatestMotionFrame.length = length;
        /* Write this last so vTask_Control never consumes a partial frame. */
        LatestMotionValid = 1;
        return;
    }

    uint8_t next = (EventQueueWrite + 1U) & PTO_EVENT_QUEUE_MASK;
    if (next == EventQueueRead)
    {
        EventQueueOverflow++;
        return;
    }

    memcpy(EventQueue[EventQueueWrite].data, data, length);
    EventQueue[EventQueueWrite].length = length;
    /* Publish the completed entry only after its data has been written. */
    EventQueueWrite = next;
}

/* Get_CMD_Flag also promotes the next command into the task-owned buffer. */
uint8_t Get_CMD_Flag(void)
{
    uint8_t has_frame = 0;
    uint32_t int_save;

    if (ActiveFrameValid != 0)
    {
        return 1;
    }

    int_save = LOS_IntLock();
    if (LatestMotionValid != 0)
    {
        memcpy(&ActiveFrame, &LatestMotionFrame, sizeof(ActiveFrame));
        LatestMotionValid = 0;
        has_frame = 1;
    }
    else if (EventQueueRead != EventQueueWrite)
    {
        memcpy(&ActiveFrame, &EventQueue[EventQueueRead], sizeof(ActiveFrame));
        EventQueueRead = (EventQueueRead + 1U) & PTO_EVENT_QUEUE_MASK;
        has_frame = 1;
    }
    LOS_IntRestore(int_save);

    ActiveFrameValid = has_frame;
    return has_frame;
}

uint8_t *Get_RxBuffer(void)
{
    return ActiveFrame.data;
}

uint8_t Get_CMD_Length(void)
{
    return ActiveFrame.length;
}

void Clear_CMD_Flag(void)
{
    ActiveFrame.length = 0;
    ActiveFrameValid = 0;
}
/********************************************************************************************************/
void Upper_Data_Receive(uint8_t Rx_Temp)
{
    switch (RxFlag)
    {
    case 0:
        if (Rx_Temp == PTO_HEAD)
        {
            RxBuffer[0] = PTO_HEAD;
            // printf("PTO_HEAD:%#2x   ", RxBuffer[0]);
            RxFlag = 1;
        }
        else
        {
            RxBuffer[0] = 0;
        }
        break;

    case 1:
        if (Rx_Temp == PTO_DEVICE_ID)
        {
            RxBuffer[1] = PTO_DEVICE_ID;
            // printf("PTO_DEVICE_ID:%#2x  ", RxBuffer[1]);
            RxFlag = 2;
            RxIndex = 2;
        }
        else
        {
            /* Preserve a new header byte so FF FF FC can re-synchronise. */
            RxBuffer[0] = (Rx_Temp == PTO_HEAD) ? PTO_HEAD : 0;
            RxFlag = (Rx_Temp == PTO_HEAD) ? 1 : 0;
        }
        break;
    case 2:
        RxLength = Rx_Temp + 2U;
        if ((RxLength < 5U) || (RxLength > PTO_MAX_BUF_LEN))
        {
            Protocol_ResetRxState();
            break;
        }
        RxBuffer[RxIndex] = Rx_Temp;
        RxIndex++;
        RxFlag = 3;
        break;

    case 3:
        RxBuffer[RxIndex] = Rx_Temp;
        RxIndex++;
        if (RxIndex >= RxLength)
        {
            Protocol_StoreFrame(RxBuffer, RxLength);
            Protocol_ResetRxState();
        }
        break;

    default:
        break;
    }
}

static osThreadId_t BeepThreadId = NULL;
static volatile uint16_t PendingBeepTime = 0;
static volatile uint8_t BeepPending = 0;

static void BeepTask(void *arg)
{
    (void)arg;
    while (1)
    {
        uint16_t time = 0;
        uint8_t pending = 0;
        uint32_t int_save = LOS_IntLock();
        if (BeepPending != 0)
        {
            time = PendingBeepTime;
            BeepPending = 0;
            pending = 1;
        }
        LOS_IntRestore(int_save);

        if (pending != 0)
        {
            bsp_beep_play(time);
        }
        else
        {
            osDelay(1);
        }
    }
}

static void Protocol_PlayBeepAsync(uint16_t time)
{
    if (BeepThreadId == NULL)
    {
        const osThreadAttr_t attr = {
            .name = "BeepThread",
            .stack_size = 1024,
            .priority = osPriorityNormal,
        };
        BeepThreadId = osThreadNew(BeepTask, NULL, &attr);
        if (BeepThreadId == NULL)
        {
            return;
        }
    }

    uint32_t int_save = LOS_IntLock();
    PendingBeepTime = time;
    BeepPending = 1;
    LOS_IntRestore(int_save);
}

typedef struct {
    uint8_t delay;
} LightParam;
static void RightLightTask(void *arg)
        {
                        LightParam *param = (LightParam *)arg;
                        set_on_right_light();
                        osDelay(param->delay);
                        set_off_right_light();
                        free(param);
        }
static void BothLightsTask(void *arg)
        {
                LightParam *param = (LightParam *)arg;
                set_on_left_light();
                set_on_right_light();
                osDelay(param->delay);
                set_off_left_light();
                set_off_right_light();
                free(param);
        }
 static void LeftLightTask(void *arg)
        {
            LightParam *param = (LightParam *)arg;
            set_on_left_light();
            osDelay(param->delay);
            set_off_left_light();
            free(param);
        }

/********************************************************************************************************/
// 解析数据
void Upper_Data_Parse(uint8_t *data_buf, uint8_t num)
{
    // 包头1 包头2 数量 功能 参数x 校验和
    /* 首先计算校验累加和 */
    int sum = 0;
    for (uint8_t i = 2; i < (num - 1); i++)
        sum += *(data_buf + i);
    sum = sum & 0xFF;
    /* 判断校验累加和 若不同则舍弃*/
    uint8_t recvSum = *(data_buf + num - 1);
    if (!(sum == recvSum))
    {
        printf("Check sum error!, CalSum:%d, recvSum:%d\n", sum, recvSum);
        for (uint8_t i = 0; i < num; i++)
        {
            printf("data_buf[%d]:%#2x    ", i, data_buf[i]);
        }
        printf("\n");
        return;
    }

    uint8_t func_id = *(data_buf + 3);

    switch (func_id)
    {
    /* 判断功能字：蜂鸣器控制 */
    case FUNC_BEEP:
    {
        uint16_t time = *(data_buf + 5) << 8 | *(data_buf + 4);
        // printf("beep:%d\n", time);
        Protocol_PlayBeepAsync(time);
        break;
    }
    /* 控制电机，未使用编码器 */
    case FUNC_MOTOR:
    {
        int16_t speed[4] = {0};
        int8_t motor_1 = *(data_buf + 4);
        int8_t motor_2 = *(data_buf + 5);
        int8_t motor_3 = *(data_buf + 6);
        int8_t motor_4 = *(data_buf + 7);

        // printf("motor=%d, %d, %d, %d", motor_1, motor_2, motor_3, motor_4);

        int16_t motor_pulse = MOTOR_MAX_PULSE - MOTOR_IGNORE_PULSE;
        speed[0] = (int16_t)motor_1 * (motor_pulse / 100.0);
        speed[1] = (int16_t)motor_2 * (motor_pulse / 100.0);
        speed[2] = (int16_t)motor_3 * (motor_pulse / 100.0);
        speed[3] = (int16_t)motor_4 * (motor_pulse / 100.0);
        // PWM控制小车运动
        Motion_Set_Pwm(speed[0], speed[1], speed[2], speed[3]);
        break;
    }
    /* 控制小车运动 */
    case FUNC_CAR_RUN:
    {
        uint8_t parm = *(data_buf + 4);
        uint8_t state = *(data_buf + 5);
        uint16_t speed = *(data_buf + 7) << 8 | *(data_buf + 6);
        // printf("car_run=0x%02X, %d, %d", parm, state, speed);
        uint8_t adjust = parm & 0x80;
        Motion_Ctrl_State(state, speed, (adjust == 0 ? 0 : 1));
        break;
    }

    /* 判断功能字：小车速度设置 */
    case FUNC_MOTION:
    {
        uint8_t parm = (uint8_t)*(data_buf + 4);
        int16_t Vx_recv = *(data_buf + 6) << 8 | *(data_buf + 5);
        int16_t Vy_recv = *(data_buf + 8) << 8 | *(data_buf + 7);
        int16_t Vz_recv = *(data_buf + 10) << 8 | *(data_buf + 9);
        uint8_t adjust = parm & 0x80;
        // printf("motion: 0x%02X, %d, %d, %d\n", parm, Vx_recv, Vy_recv, Vz_recv);

        if (Vx_recv == 0 && Vy_recv == 0 && Vz_recv == 0)
        {
            Motion_Stop(STOP_BRAKE);
        }
        else
        {
            Motion_Ctrl(Vx_recv, Vy_recv, Vz_recv, (adjust == 0 ? 0 : 1));
        }
        break;
    }

    /* 判断功能字：PID参数设置 */
    case FUNC_SET_MOTOR_PID:
    {
        uint16_t kp_recv = *(data_buf + 5) << 8 | *(data_buf + 4);
        uint16_t ki_recv = *(data_buf + 7) << 8 | *(data_buf + 6);
        uint16_t kd_recv = *(data_buf + 9) << 8 | *(data_buf + 8);
        uint8_t args = *(data_buf + 10);
        float kp = kp_recv / 1000.0;
        float ki = ki_recv / 1000.0;
        float kd = kd_recv / 1000.0;
        // printf("pid:%.2f, %.2f, %.2f, args:0x%02X\n", kp, ki, kd, args);
        PID_Set_Motor_Parm(MAX_MOTOR, kp, ki, kd);
        // if (args == SAVE_VERIFY)
        // {
        //     Flash_Set_PID(MAX_MOTOR, kp, ki, kd);
        // }
        break;
    }
    /* 判断功能字：偏航角PID参数设置 */
    case FUNC_SET_YAW_PID:
    {
        uint16_t kp_recv = *(data_buf + 5) << 8 | *(data_buf + 4);
        uint16_t ki_recv = *(data_buf + 7) << 8 | *(data_buf + 6);
        uint16_t kd_recv = *(data_buf + 9) << 8 | *(data_buf + 8);
        uint8_t forever = *(data_buf + 10);
        float kp = kp_recv / 1000.0;
        float ki = ki_recv / 1000.0;
        float kd = kd_recv / 1000.0;
        // printf("YAW PID:%.2f, %.2f, %.2f, args:0x%02X\n", kp, ki, kd, forever);
        PID_Yaw_Set_Parm(kp, ki, kd);
        // if (forever == SAVE_VERIFY)
        // {
        //     Flash_Set_Yaw_PID(kp, ki, kd);
        // }
        break;
    }
    /* 判断功能字：寻线模式 */
    case FUN_CAR_TRACE:
    {
        uint8_t parm = (uint8_t)*(data_buf + 4);
        // printf("[trace:%#02x]\n", parm);
        Trace_Ctrl(parm);
        break;
    }
    case FUNC_Light_Control:
    {
        uint8_t parm1 = *(data_buf + 4);
        uint8_t parm2 = *(data_buf + 5);
        uint8_t time = *(data_buf + 6);
        // bsp_beep_play(100);
        
        // printf("Light Control:0x%02X\n", parm);
        // void set_on_left_light(void);
        // void set_on_right_light(void);
        // void set_off_left_light(void);
        // void set_off_right_light(void);
        // void car_light_power_on(void);
        if (parm1 == 0x03){
            if(time == 0x00) return;
                
            // 创建线程同时控制左右灯
            osThreadAttr_t attr = {
                .name = "BothLightsThread",
                .stack_size = 1024,
                .priority = osPriorityNormal,
            };

            LightParam *param = malloc(sizeof(LightParam));
            if (param) {
                param->delay = time;
                osThreadNew(BothLightsTask, param, &attr);
            }
            return;
        }
        if (time == 0x00){
            if (parm1 == 0x01){
                if (parm2 == 0x01)
                    set_on_left_light();
                else if (parm2 == 0x02)
                    set_on_right_light();
            }else if (parm1 == 0x02){
                if (parm2 == 0x01)
                    set_off_left_light();
                else if (parm2 == 0x02)
                    set_off_right_light();
            }
        }else{
            if (parm1 == 0x01){
                if (parm2 == 0x01) {
                    // 创建线程控制左灯
                    osThreadAttr_t attr = {
                        .name = "LeftLightThread",
                        .stack_size = 1024,
                        .priority = osPriorityNormal,
                    };
                    LightParam *param = malloc(sizeof(LightParam));
                    if (param) {
                        param->delay = time;
                        osThreadNew(LeftLightTask, param, &attr);
                    }
                }else if (parm2 == 0x02) {
                    // 创建线程控制右灯
                    osThreadAttr_t attr = {
                        .name = "RightLightThread",
                        .stack_size = 1024,
                        .priority = osPriorityNormal,
                    };
                    LightParam *param = malloc(sizeof(LightParam));
                    if (param) {
                        param->delay = time;
                        osThreadNew(RightLightTask, param, &attr);
                    }
                }
            }
        }
        break;
    }
    default:
        break;
    }
}
