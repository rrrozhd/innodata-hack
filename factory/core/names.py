"""Name pools for invented but plausible engineering content.

Nothing here refers to a real product, so no label can be answered from memory.
"""
from __future__ import annotations

from .seed import pick

# Peripherals a robot controller typically talks to over a serial bus.
DEVICES = ["IMU", "FLASH", "ADC", "DAC", "ENC", "DRV", "EEPROM", "BARO", "MAG", "GYRO", "RADIO", "LCD"]

# (clock, controller-to-device, device-to-controller) naming schemes seen on schematics.
SPI_NAMES = [
    ("SCLK", "MOSI", "MISO"),
    ("SCK", "MOSI", "MISO"),
    ("SPI1_SCK", "SPI1_MOSI", "SPI1_MISO"),
    ("SCLK", "COPI", "CIPO"),
    ("SCK", "SDI", "SDO"),
]

DISTRACTORS = ["DRDY", "INT1", "INT2", "RESET#", "LED_PWM", "STEP", "DIR", "FAULT#", "TACH", "BTN_A", "BUZZER",
               "FAN_PWM", "ESTOP#", "HEARTBEAT"]

CAPTURE_PROJECTS = ["gimbal_bringup", "arm_joint3_debug", "rover_imu_spi", "gripper_fw_test", "flightctl_bus",
                    "agv_encoder_check", "leg_driver_rev_b", "pan_tilt_comms", "wheel_odometry", "tool_changer_io"]

SAMPLE_RATES = ["50 MS/s", "100 MS/s", "125 MS/s", "250 MS/s", "500 MS/s"]

ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]


def cs_name(rng, device: str) -> str:
    """Active-low chip select; the trailing # marks active low, as on schematics."""
    return pick(rng, ["{d}_CS#", "{d}_SS#", "CS_{d}#"]).format(d=device)


def capture_title(rng) -> str:
    return f"{pick(rng, CAPTURE_PROJECTS)}_{int(rng.integers(1, 40)):02d}.capture"
