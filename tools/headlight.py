import time
import serial

PORT = "COM5"

BEEP_100MS = bytes.fromhex("FF FC 05 02 64 00 6B")
LEFT_ON = bytes.fromhex("FF FC 06 70 01 01 00 78")
RIGHT_ON = bytes.fromhex("FF FC 06 70 01 02 00 79")
LEFT_OFF = bytes.fromhex("FF FC 06 70 02 01 00 79")
RIGHT_OFF = bytes.fromhex("FF FC 06 70 02 02 00 7A")

def send(ser, label, frame):
	print(f"{label}: {frame.hex(' ').upper()}")
	ser.write(frame)
	ser.flush()

ser = serial.Serial()
ser.port = PORT
ser.baudrate = 115200
ser.bytesize = serial.EIGHTBITS
ser.parity = serial.PARITY_NONE
ser.stopbits = serial.STOPBITS_ONE
ser.timeout = 0.2
ser.rtscts = False
ser.dsrdtr = False
ser.dtr = False
ser.rts = False
ser.open()

try:
	send(ser, "蜂鸣器 100 ms", BEEP_100MS)
	time.sleep(1)

	send(ser, "左灯开", LEFT_ON)
	time.sleep(2)

	send(ser, "右灯开", RIGHT_ON)
	input("应为双灯持续点亮；按回车后关灯：")

	send(ser, "左灯关", LEFT_OFF)
	time.sleep(0.1)
	send(ser, "右灯关", RIGHT_OFF)
finally:
	ser.close()