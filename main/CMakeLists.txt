idf_component_register(
    SRCS "main.cpp" "sn2_protocol.cpp"
    INCLUDE_DIRS "."
    REQUIRES vfd
    PRIV_REQUIRES esp_driver_usb_serial_jtag esp_timer
)
