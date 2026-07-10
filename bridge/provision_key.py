"""Escreve a chave AES persistida em device_key.env na characteristic
aesKeyChar do dispositivo real, via BLE (script unico de provisioning)."""
import asyncio
import sys
from pathlib import Path
from bleak import BleakClient, BleakScanner

UUID_AES_KEY = "abcd1234-5678-1234-5678-abcdef123456"
DEVICE_NAME = "Wearable"

def load_key_hex():
    for line in Path("device_key.env").read_text().splitlines():
        if line.startswith("CAREWEAR_AES_KEY_HEX="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("CAREWEAR_AES_KEY_HEX nao encontrada em device_key.env")

async def main():
    key = bytes.fromhex(load_key_hex())
    assert len(key) in (16, 24, 32)
    print(f"[PROV] chave carregada de device_key.env ({len(key)} bytes)")
    print("[PROV] a procurar dispositivo...")
    dev = None
    for _ in range(6):
        devices = await BleakScanner.discover(timeout=5.0)
        for d in devices:
            if d.name == DEVICE_NAME:
                dev = d
                break
        if dev:
            break
    if not dev:
        print("[PROV] dispositivo nao encontrado")
        sys.exit(1)
    print(f"[PROV] encontrado {dev.address} - a ligar...")
    async with BleakClient(dev.address) as client:
        await client.write_gatt_char(UUID_AES_KEY, key, response=True)
        print("[PROV] chave AES escrita com sucesso")

asyncio.run(main())
