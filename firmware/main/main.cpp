#include <hal/hal.h>
#include <mooncake_log.h>

extern "C" void app_main(void)
{
    mclog::set_level(mclog::level_info);
    mclog::set_time_format(mclog::time_format_unix_milliseconds);

    GetHAL().init();
    GetHAL().startXiaozhi();
}
