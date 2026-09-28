# Schematic overview

This is an implementation map for the final reviewed architecture. It does not replace [`architecture_final.md`](architecture_final.md), which is the source for exact values, part numbers, and validation gates.

## Net-level block topology

```mermaid
flowchart LR
  J1[J1 USB-C upstream] -->|D+/D-/GND| U1[U1 CH334F USB 2.0 hub]
  J1 -->|VBUS_UP| U14
  J2[J2 USB-C 5 V power] -->|VBUS_PWR / preferred| U14[U14 TPS2121RUXR]
  U14 -->|VSYS| U4[U4 TPS259470L]
  U4 -->|VBUS_HIL| J3[J3 USB-C DUT]
  U1 -->|Port 1| U2[U2 CP2102N]
  U1 -->|Port 2| U5[U5 TS3USB221A]
  U5 -->|D+/D-| J3
  U2 -->|GPIO.6 HIL_VBUS_EN| U4
  U2 -->|GPIO.4 HIL_DATA_EN_N| U5
  U2 -->|GPIO.2 HIL_CC_EN_N| U6[U6 SN74LVC1G125]
  U6 -->|fixed 3 A Rp| J3
  U14 --> U8[U8 3.3 V LDO]
  U8 --> U1
  U8 --> U2
  U8 --> U5
  U8 --> U6
```

## Sheet / placement partitions

| Partition | Contents | Critical routing / placement rule |
|---|---|---|
| J1 upstream and hub | J1, U9, U1, Y1, Q6/Q7 | Keep USB pairs short and 90 Ohm differential. Keep Y1 away from high-current copper. |
| Power entry and priority | J2, input TVS, U14, U8 | J2 is U14 IN2; J1 is IN1. Strap PR1 to GND and CP2 to J2 VBUS. Keep VSYS copper wide and U14 thermal vias under pad. |
| HIL output | U4, C9-C13, Q2/Q3, R36, J3 VBUS | Put input/output capacitors at U4 pins. Keep the VBUS_HIL and discharge loop compact. |
| DUT data and CC | U5, U6, U10, Rp resistors, J3 | Keep U5 USB routing continuous and short. Place U10 directly behind J3. Keep CC traces away from D+/D-. |
| Control and test | U2, sense dividers, FLT pull-up, test points | Bring all enables, senses, ILM, FLT, VSYS and both input VBUS nets to labelled test points. |

## PCB floor plan

- Left edge: J1; then U9 and U1/Y1 toward the left-center.
- Top edge: J2, input TVS, U14 and its VSYS bulk capacitors.
- Top-right: U4, output capacitors and the Q2/Q3 discharge loop.
- Right edge: J3 with U10 immediately behind it; U5/U6 between U1 and J3.
- Bottom-center: U2; place U8 next to its 3.3 V loads.
- L2 is solid ground. L3 carries VSYS and VBUS_HIL pours. Use a JLC-selected four-layer stack-up and its calculated 90 Ohm USB differential geometry.

## Build gates

- No KiCad files are included in this package.
- Use the exact component selections and pin-level constraints in [`architecture_final.md`](architecture_final.md).
- Prototype/production validation gates remain mandatory before release: CP2102N configuration/read-back, CH334F reset behavior, U14 reverse current, 3 A thermal/load-step behavior, J2 source compatibility, and ESD robustness.
