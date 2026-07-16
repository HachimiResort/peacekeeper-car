# SLHSP104_AT32 板卡电量检测

## 原理

锂电池电压与剩余电量在常用工作区间内近似线性，因此可通过采集电池电压、按比例换算出电量百分比。板卡使用 `PC0` 引脚读取电池 ADC 值。

![PC0 电池 ADC 检测](./media/image.png)

![电池检测示意](./media/42ccaf3cbc32f22e44957176a30d465.jpg)

## 分压与换算

板卡的电池检测分压电路使用 `3.3 kΩ` 和 `10 kΩ` 两个电阻。若电池电压范围为 `x V` 到 `y V`，ADC 端的电压范围为：

```text
3.3x / (10 + 3.3) ～ 3.3y / (10 + 3.3)
```

![电池检测分压电路](./media/image.png)

下面的常量与公式可用于计算电量百分比。请根据实际电池类型和硬件分压电阻调整阈值与比例系数。

```c
// 电池满电电压
#define BATTERY_FULL_VOL          12.6f

// 电池低电压阈值
#define BATTERY_LACK_VOL          10.5f

// BATTERY_SCALE_FACTOR = (3.3 + 10) / 3.3，取决于硬件分压电路
#define BATTERY_SCALE_FACTOR      (13.3f / 3.3f)

// 电量百分比
(100.0f * (adc_value * 3.3f / 4096.0f * BATTERY_SCALE_FACTOR - BATTERY_LACK_VOL)
 / (BATTERY_FULL_VOL - BATTERY_LACK_VOL))
```
