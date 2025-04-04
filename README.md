# nrf52-ble-dfu

This is a python package to demo BLE device firmware updates for the nrf52 series devices.

It is compatible with the (now deprecated) nRF5 SDK, tested with v17.0.2, using `SDK_ROOT/examples/dfu/secure_bootloader`.

# testing

The test target is the nRF52840 DK, flashed with `secure_bootloader` using Segger Embedded Studio.
The test update package is a slightly modified version of the `ble_blinky` example found in `examples/ble_peripheral/`.

To build the demo application update package, download the [`nrfutil` tool](https://www.nordicsemi.com/Products/Development-tools/nRF-Util) from nordic and install the `nrf5sdk-tools` extension.

Then the application package can be built with the command:
```bash
nrfutil pkg generate \
	--key-file private.key.pem      \
	--application-version 0x01      \
	--bootloader-version 0x01       \
	--hw-version 52                 \
	--sd-req 0x100                  \
	--sd-id 0x100                   \
	--application <application.hex> \
	# --bootloader <bootloader.hex>   \
	# --softdevice <softdevice.hex>   \
	package.zip
```

Run `nrfutil pkg generate --help` for more information.

The resulting zip file is the update package that contains everything to be sent to the device.

Note that the private key must be in pem format and correspond to the public key hard-coded into the bootloader in `dfu_public_key.c`.

The `nrf52_ble_dfu` module can be run as a script to automatically pair with the default bootloader target "DfuTarg" given the dfu package zipfile:
```bash
python -m nrf52_ble_dfu /path/to/package.zip
```

