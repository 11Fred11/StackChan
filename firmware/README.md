
## Build

### Fetch Dependencies

```bash
python3 ./fetch_repos.py
```

Dependencies come from forks under [github.com/11Fred11](https://github.com/11Fred11), pinned in `repos.json`.
Stack-chan's changes to Xiaozhi live on the `stackchan` branch of
[11Fred11/xiaozhi-esp32](https://github.com/11Fred11/xiaozhi-esp32/tree/stackchan); edit them there, not as a patch.

### Point the robot at your own gateway

Copy `sdkconfig.defaults.local.example` to `sdkconfig.defaults.local` (git-ignored) and set `CONFIG_OTA_URL`
to your gateway (see `backend/`). Delete `sdkconfig` so the new defaults apply, then build.

### Tool Chains

[ESP-IDF v5.5.4](https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32s3/index.html)

### Build

```bash
idf.py build
```

### Host-side tests

The motion coordinate helpers can be tested without ESP-IDF hardware:

```bash
cmake -S tests -B build-host-tests
cmake --build build-host-tests
ctest --test-dir build-host-tests --output-on-failure
```

### Flash

```bash
idf.py flash
```
