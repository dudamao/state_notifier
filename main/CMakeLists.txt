set(main_srcs
    "main.cpp"
    "sn2_protocol.cpp"
    "clock_source.cpp"
    "clock_text.cpp"
    "display.cpp"
    "font5x7.cpp"
    "net_session.cpp"
    "scheduler.cpp"
    "text_render.cpp"
    "time_sync.cpp"
)

# PRIV_REQUIRES must not depend on configuration options (requirements are
# expanded before the configuration is loaded), so esp_driver_usb_serial_jtag
# is listed unconditionally. Its objects are unreferenced and dropped by the
# linker unless usb_display.cpp is compiled in below.
set(main_priv_requires esp_timer esp_event esp_netif esp_wifi nvs_flash
    esp_driver_usb_serial_jtag
)

# The SN2 USB endpoint is the optional legacy feature. Without it the USB
# Serial/JTAG driver is never installed and USB is power-only.
if(CONFIG_STATE_NOTIFIER_USB_DISPLAY)
    list(APPEND main_srcs "usb_display.cpp")
endif()

idf_component_register(
    SRCS ${main_srcs}
    INCLUDE_DIRS "."
    REQUIRES vfd
    PRIV_REQUIRES ${main_priv_requires}
)
