import time
import sys
import glob
import serial

# the global vars of the devices
ser = serial.Serial()

DEBUG = False

# SCPI Addresses:
# Current source: USB, prologix USB-GPIB. Hence: not via pyvisa, as that is not stable for that adapter.
ADDR_SOURCE_DEFAULT = "/dev/cu.usbmodem11101"
AUTOREAD = False
# serial timeout: 20ms minimum
SERIAL_TIMEOUT = 0.02
HAS_OPT760 = True


def serial_ports():
    """ Lists serial port names

        :raises EnvironmentError:
            On unsupported or unknown platforms
        :returns:
            A list of the serial ports available on the system
    """
    if sys.platform.startswith('win'):
        ports = ['COM%s' % (i + 1) for i in range(256)]
    elif sys.platform.startswith('linux') or sys.platform.startswith('cygwin'):
        # this excludes your current terminal "/dev/tty"
        ports = glob.glob('/dev/cu[A-Za-z]*')
    elif sys.platform.startswith('darwin'):
        ports = glob.glob('/dev/cu.usbmodem*')
    else:
        raise EnvironmentError('Unsupported platform')

    result = []
    for port in ports:
        try:
            s = serial.Serial(port)
            s.close()
            result.append(port)
        except (OSError, serial.SerialException):
            pass
    return result


def sendSerialCmdRaw(cmd):
    bcmd = bytearray()
    bcmd.extend(cmd.encode("ascii"))
    bcmd.append(0x0D)  # CR
    bcmd.append(0x0A)  # LF
    ser.write(bcmd)


def sendSerialCmd(cmd, readReply=True, delaysecs=0):
    global ser
    if DEBUG:
        if not readReply:
            print(f"Sending: {cmd}")
        else:
            print(f"Sending: {cmd} : ", end="")
    sendSerialCmdRaw(cmd)
    if readReply:
        if delaysecs > 0:
            time.sleep(delaysecs)
        if not AUTOREAD:
            sendSerialCmdRaw("++read eoi")
        s = ser.read(256)
        retstr = s.decode("ascii")
        if DEBUG:
            print(f"{retstr} ({len(s)}b)")
        return retstr
    else:
        s = ser.read(256)
        return None


def inst_cs_query(cmd):
    delaysecs = 0
    if cmd.startswith("MEAS"):
        # 40msec minimum
        delaysecs = 0.05
    return sendSerialCmd(cmd, True, delaysecs)


def inst_cs_write(cmd):
    return sendSerialCmd(cmd, False)


def inst_cs_init_serial():
    global ser

    port = None
    baudrate = 38400  # 115200
    
    ports = serial_ports()
    if len(ports) == 0:
        print('ERROR: No serial ports found')
        return False

    if (len(ports) > 1):
        if ADDR_SOURCE_DEFAULT in ports:
            port = ADDR_SOURCE_DEFAULT
        else:
            print(f'ERROR: Found multiple ports, and the default port is not in it. Found {ports}. Select the port.')
            return False
    
    if len(ports) == 1:
        port = ports[0]

    print(f"Using Prologix device on port \"{port}\".")
    ser = serial.Serial(port, baudrate=baudrate, timeout=SERIAL_TIMEOUT)

    # using a fenrir GPIB-USB clone. Manual: https://github.com/fenrir-naru/gpib-usbcdc/blob/master/docs/manual_by_Alessandro.pdf
    sendSerialCmd("++mode 1", False)  # controller mode (the only mode it supports)
    if AUTOREAD:
        sendSerialCmd("++auto 1", False)  # no need for "++read eoi"
    else:
        sendSerialCmd("++auto 0", False)  # need for "++read eoi"
    sendSerialCmd("++eos 0", False)  # CR/LF is oes
    sendSerialCmd("++read", False)  # read all data until timeout. fenrir FW specific.
    return True


def inst_cs_init_device(addr):
    
    sendSerialCmd("++addr " + str(addr))
    
    inst_cs_write("*CLS")
    # check ID
    s = inst_cs_query("*IDN?").strip()
    if len(s) > 0:
        print(f"Device {addr} = {s}")
        if ("6631B" not in s) and ("6632B" not in s) and ("66332A" not in s) and ("6633B" not in s) and ("6634B" not in s):
            print(f'ERROR: device ID is unexpected: "{s}"')
            return False
    else:
        print(f"Device {addr} is absent")
        return False

    # output off, voltage 0V, current 0
    inst_cs_write("OUTP 0")
    if HAS_OPT760:
        inst_cs_write("OUTP:REL:POL NORM")
    inst_cs_write("SOUR:VOLT 0")
    inst_cs_write("SOUR:CURR 0")
    s = inst_cs_query("SYST:ERR?").strip()
    if not s.startswith("+0"):
        print(f'ERROR during init_device({addr}): "{s}"')
        return False

    return True


def getMaxVolts():
    s = inst_cs_query("*IDN?").strip()
    if "6631B" in s:
        return 9.190
    if ("6632B" in s) or ("66332A" in s):
        return 20.475
    if "6633B" in s:
        return 51.188
    if "6634B" in s:
        return 102.38
    return 0


def getMinVolts():
    if HAS_OPT760:
        return -1 * getMaxVolts()
    else:
        return 0


def getMaxAmps():
    s = inst_cs_query("*IDN?").strip()
    if "6631B" in s:
        return 10.237
    if ("6632B" in s) or ("66332A" in s):
        return 5.1188
    if "6633B" in s:
        return 2.0475
    if "6634B" in s:
        return 1.0238
    return 0


def getMinAmps():
    if HAS_OPT760:
        return -1 * getMaxAmps()
    else:
        return 0


def inst_cs_close():
    inst_cs_write("OUTP 0")
    if HAS_OPT760:
        inst_cs_write("OUTP:REL:POL NORM")
    inst_cs_write("SOUR:VOLT 0")
    inst_cs_write("SOUR:CURR 0")
    

def initMeasurements():
    inst_cs_write("OUTP 1")
    # in case you shorted, let CC mode activate
    time.sleep(1)
    

def closeMeasurements():
    inst_cs_close()


# sets the current, and lets the PSU settle some time. This PSU has a tendency to take time to go to CC mode.
def setVoltage(val, oldval=None):
    bNegative = False
    sleeptime_s = 0
    setval = abs(val)
    if val < 0:
        bNegative = True
        if oldval is None or oldval >= 0:
            inst_cs_write("OUTP:REL:POL REV")
            sleeptime_s += 0.4
    else:
        if oldval is None or oldval < 0:
            inst_cs_write("OUTP:REL:POL NORM")
            sleeptime_s += 0.4

    if sleeptime_s > 0:      
        time.sleep(sleeptime_s)
        sleeptime_s = 0

    inst_cs_write(f"SOUR:VOLT {setval:.5f}")
    #s = inst_cs_query("SYST:ERR?").strip()
    #if not s.startswith("+0"):
    #    print(f'ERROR during setVoltage({val}): "{s}"')
    #    return False
    # time.sleep(sleeptime_s)
    #s = inst_cs_query("*OPC?").strip()
    #s = inst_cs_query("SYST:ERR?").strip()
    #if not s.startswith("+0"):
    #    print(f'ERROR during setVoltage({val}) OPC: "{s}"')
    #    return False
    #print(f"setVoltage({val}) OPC={s}")    
    v = inst_cs_query("MEAS:VOLT?").strip()
    #s = inst_cs_query("SYST:ERR?").strip()
    #if not s.startswith("+0"):
    #    print(f'ERROR during setVoltage({val}) readback: "{s}"')
    #    return False
    try:
        f = float(v)
    except:
        print(f'ERROR during setVoltage({val}) readback: "{v}", not a float')
        return False   
    if bNegative:
        f = f * -1
    print(f"Set V={val:.3f}, Measured V={f:.3f}, offset={(f-val):.3f}")
    return True


def setCurrent(val, oldval=None):
    sleeptime_s = 0.1
    if val < 0:
        if oldval is None or oldval >= 0:
            inst_cs_write("OUTP:REL:POL REV")
            sleeptime_s += 0.4
        val = abs(val)
    else:
        if oldval is None or oldval < 0:
            inst_cs_write("OUTP:REL:POL NORM")
            sleeptime_s += 0.4

    inst_cs_write(f"SOUR:CURR {val:.5f}")
    time.sleep(sleeptime_s)


def readDevices(test):
    global inst_cm
    global inst_target

    print("Opening PSU.")
    if not inst_cs_init_serial():
        return 1
    
    for addr in range(1, 5):
        if inst_cs_init_device(addr):
            print(f"Device {addr}: initialized.")

            initMeasurements()
            maxv = getMaxVolts()
            minv = getMinVolts()
            stepv = (maxv - minv) / 20
            if stepv < 1:
                stepv = 1
            
            print("Looping through voltages:")
            oldv = None
            v = minv
            while v <= maxv:
                if not setVoltage(v, oldv):
                    break
                oldv = v
                v += stepv
            print("Closing")
            closeMeasurements()


if __name__ == "__main__":
    # set param to True to force a short test
    readDevices(False)
