idf_component_register(
    SRCS "vfd_display.cpp"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES esp_driver_gpio esp_driver_spi
)
