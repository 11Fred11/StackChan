#include <hal/hal.h>
#include <mooncake_log.h>
#include <ssid_manager.h>

#define WIFI_SSID "Livebox-BD4A"
#define WIFI_PASSWORD "r2szcn7GdJ7ZzcnGKZ"

extern "C" void app_main(void)
{
    mclog::set_level(mclog::level_info);
    mclog::set_time_format(mclog::time_format_unix_milliseconds);

    GetHAL().init();

    SsidManager::GetInstance().AddSsid(WIFI_SSID, WIFI_PASSWORD);

    GetHAL().startXiaozhi();
}
