#!/usr/bin/env python

import glob
import serial
import slip
import os
import subprocess
import sys
import time
import filecmp
import logging
import logging.config

sololink_conf = "/etc/sololink.conf"

ARTOO_SYSINFO_ID = chr(0x3)
ARTOO_UPDATE_ID = chr(0x12)
ARTOO_LOCKOUT_ID = chr(0x13)

ARTOO_UPDATE_SUCCESS = chr(1)
ARTOO_UPDATE_FAILED = chr(2)

ARTOO_LOCKOUT_FALSE = chr(0)
ARTOO_LOCKOUT_TRUE = chr(1)

# update_result should be either ARTOO_UPDATE_SUCCESS or ARTOO_UPDATE_FAILED
def setArtooUpdateComplete(update_result):
    ser = serial.Serial("/dev/ttymxc1", 115200, timeout=1)
    slipdev = slip.SlipDevice(ser)
    slipdev.write("".join([ARTOO_UPDATE_ID, update_result]))
    ser.close()

# lockout should be either ARTOO_LOCKOUT_FALSE or ARTOO_LOCKOUT_TRUE
def setArtooLockout(lockout):
    ser = serial.Serial("/dev/ttymxc1", 115200, timeout=1)
    slipdev = slip.SlipDevice(ser)
    slipdev.write("".join([ARTOO_LOCKOUT_ID, lockout]))
    ser.close()

def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError:
        pass # already exists

def doUpdateComplete():
    setArtooLockout(ARTOO_LOCKOUT_FALSE)
    if not os.path.exists("/log/updates/READY"):
        if os.path.exists("/log/updates/UPDATEFAILED"):
            f = open("/log/updates/UPDATEFAILED", "r")
            # file should be one line; read it all
            r = f.read(1000)
            f.close()
            r = r.strip("\r\n\t\0 ")
            logger.info("request \"update failed\" screen (%s)", r)
            setArtooUpdateComplete(ARTOO_UPDATE_FAILED)
        else:
            logger.info("request \"update success\" screen")
            setArtooUpdateComplete(ARTOO_UPDATE_SUCCESS)
        mkdir_p("/log/updates")
        open("/log/updates/READY", "w").close() # "touch"
    else:
        logger.info("no screen update (READY exists)")

# return tuple (filename, version), or None
def getFirmwareInfo():
    files = sorted(glob.glob("/firmware/artoo_*.bin"))
    if not files:
        return None
    filename = files[-1]
    # Filename may be of the form
    # "/firmware/artoo_0.0.0.bin", or
    # "/firmware/artoo_v0.0.0.bin".
    # Get it without the 'v'.
    if filename[16] == 'v':
        version = filename[17:-4]
    else:
        version = filename[16:-4]
    return (filename, version)

# return version as string ("unknown" if can't get version)
def getArtooVersion():
    #Check the version of the stm32 firmware over serial
    #The STM32 might be emitting packets already, so we try a few times to get the
    #version string. This has been observed to get the version with one retry on
    #several occasions.
    logger.info("requesting stm32 version")
    version = "unknown"
    ser = serial.Serial("/dev/ttymxc1", 115200, timeout=1)
    slipdev = slip.SlipDevice(ser)
    for i in range(5):
        slipdev.write("".join([ARTOO_SYSINFO_ID]))
        pkt = slipdev.read()
        if not pkt:
            logger.info("no data received from stm32, retrying")
            continue
        pkt = "".join(pkt)
        if pkt[0] == ARTOO_SYSINFO_ID:
            # SysInfo packet is:          artoo/src/hostprotocol.cpp
            # start size
            #   0     1  ARTOO_SYSINFO_ID artoo/src/hostprotocol.h
            #   1    12  UniqueId         artoo/src/stm32/sys.h
            #  13     2  hwversion        artoo/src/hostprotocol.cpp
            #  15   var  Version          artoo/src/version.h
            # Version may start with an initial 'v', e.g. v0.6.10,
            # but we want it starting with the numeric part.
            if pkt[15] != 'v':
                version = pkt[15:]
            else:
                version = pkt[16:]
            break
        logger.info("got %s/%d, retrying", str(hex(ord(pkt[0]))), len(pkt))
    ser.close()
    return version

def updateStm32(filename):
    s = subprocess.check_output(["stm32loader.py", "-wvq", "-s", "127",
                                 "-b", "115200", "-p", "/dev/ttymxc1", filename],
                                stderr=subprocess.STDOUT)
    # this might be ugly, but it gets it in the log
    s = s.strip("\r\n\t\0 ")
    logger.info(s)
    #Wait a second for the STM32 to come back up before we send it a message later
    time.sleep(1)

def writeVersionFile(version):
    f = open("/STM_VERSION", 'w')
    f.write(version + '\n')
    f.close()

# return version from /STM_VERSION
def getVersionFile():
    try:
        f = open("/STM_VERSION", 'r')
        version = f.readline()
        f.close()
        version = version.strip()
        if version == "":
            version = "unknown"
    except:
        version = "unknown"
    return version


logging.config.fileConfig(sololink_conf)
logger = logging.getLogger("stm32")

logger.info("stm32 update starting")

firmware = getFirmwareInfo()

if firmware is None:
    logger.info("no firmware available for update")
else:
    logger.info("firmware: file %s, version %s", firmware[0], firmware[1])

# Read the version from the STM32
artoo_version = getArtooVersion()
logger.info("running version: %s", artoo_version)

# If we have firmware and it does not match what is running, update the STM32
if firmware is not None:
    if artoo_version != firmware[1]:
        logger.info("updating")
        updateStm32(firmware[0])
        # re-read the version from the running firmware
        artoo_version = getArtooVersion()
    else:
        logger.info("not updating (new firmware is already running)")
    # Whether we used it or not, we are done with the new firmware
    logger.info("moving firmware to loaded")
    mkdir_p("/firmware/loaded")
    os.rename(firmware[0], "/firmware/loaded/" + os.path.basename(firmware[0]))
else:
    logger.info("not updating (no new firmware)")

# Write version retrieved from STM32 to file
logger.info("writing STM_VERSION with running version %s", artoo_version)
writeVersionFile(artoo_version)

doUpdateComplete()

# delete /log/.factory if it exists (it has no effect)
if os.path.exists("/log/.factory"):
    logger.info("deleting .factory")
    os.system("rm -f /log/.factory")
