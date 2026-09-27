# USB-C HIL Control Schematic Review

## Verdict

**Conditional schematic pass.** Current ERC reports **0 errors** and 12 warnings. The remaining warnings require schematic cleanup and datasheet/library confirmation; prototype and PCB-layout gates still apply before fabrication.

## Review Basis

- Architecture: `../architecture/architecture_final.md`
- Structured extraction: root sheet plus `01_power_cc`, `02_usb_hub_control`, `03_dut_interface`, and `04_test_validation`
- Datasheet context available for U1, U2, U4, U8, U9/U10/U11, U14, J1/J2/J3, Q2/Q3, and U6
- Native KiCad ERC recheck: 12 messages, including 0 errors and 12 warnings
- Cross-sheet claims were checked against the flattened KiCad netlist.

## Blocker Register

| ID | Status | Blocker | Evidence | Close When |
|---|---|---|---|---|
| Resolved | Resolved | U4 ILM resistor R31 was disconnected. | R31.1 resolves to `U4_ILM`; R31.2 resolves to GND. | Validate trip current on prototype. |
| Resolved | Resolved | U1 external-3.3 V supply topology generated a power-output conflict. | ERC no longer reports the U1/U8 output conflict; OVCUR error is also cleared. | Confirm the CH334F external-3.3 V application circuit and symbol pin model. |
| Resolved | Resolved | U14 ILIM, SS, and ST support networks were pending. | R32 = 15.4 kOhm sets about 4.23 A; C6 = 100 nF provides soft start; R33 = 10 kOhm is the ST pull-up. | Validate priority switching and inrush on prototype. |
| Resolved | Resolved | U14 VSYS bulk capacitor C5 was missing. | C5 = 47 uF is now on VSYS to GND. | Confirm effective capacitance at bias during load-step test. |
| Resolved | Resolved | J1/J2/J3 VBUS TVS devices were missing. | D1/D2 = SMAJ20A and D3 = SMAJ6.5A are now placed and connected. | Verify exact footprints, clamp behavior, and short GND returns during PCB layout. |
| Resolved | Resolved | Status indicators were missing. | LED1/LED2/LED3 with R50/R51/R52 = 1 kOhm are now placed and connected. | Verify LED polarity and current on prototype. |
| Resolved | Resolved | CH334F embedded symbol differs from the current library definition. | Pin 20 `VDD33_LDO` is intentionally `passive` for the selected external-3.3 V topology; ERC library mismatch is expected. | Retain the documented external-3.3 V application and verify it on prototype. |
| M1 | Open | Local/global net-label duplication remains. | ERC warnings remain for `EN_HIL`, `HIL_FLT_N`, `VBUS_UP`, `VBUS_PWR`, GND, and `+3.3V`. | Keep one intentional cross-sheet labeling method per net and remove stale labels/stubs. |
| M1 | Open | U14 support-network cleanup remains. | ERC warns of stale `+3V3` labels and unconnected wire stubs from the initial U14-network placement. | Remove stale labels/wires and place R33 clear of U14. |

## Confirmed Correct / Traceable Paths

| Subsystem | Result | Evidence |
|---|---|---|
| U14 priority path | Traceable | J1 is on IN1/`VBUS_UP`; J2 is on IN2/`VBUS_PWR`; output is VSYS. PR1 is GND and CP2 senses J2. Reverse-blocking remains a required prototype test. |
| U8 LDO external network | Conditional pass | IN/EN are VSYS; OUT/SNS are the external 3.3 V rail; C16/C17/C18 are present. The external-3.3 V topology is accepted electrically; the CH334F symbol still needs reconciliation. |
| U4 control path | Mostly correct | GPIO.4 `HIL_VBUS_EN` -> R27 -> `EN_HIL` -> U4 EN, with R28 pull-down. R31 now reaches U4 ILM. |
| J3 discharge | Correct topology, prototype check required | Q2/Q3, R36=470 Ohm, and R37=100 kOhm implement default discharge when EN_HIL is low. Validate discharge time and device temperature on hardware. |
| VBUS_HIL sensing | Correct | R38=10 kOhm, R39=18 kOhm, R40=1 kOhm form the expected 3.21 V nominal GPIO sense at 5 V. |
| U5 data switch | Correct | SEL is GND, OE is `HIL_DATA_EN_N`, 1D pair goes to J3, and 2D pair is intentionally NC. |
| J3 Rp | Acceptable deviation | R5/R6 are 4.7 kOhm from `VRP_3A` to CC1/CC2. This is electrically acceptable for 3 A advertisement but differs from the archived 33 kOhm parallel 5.6 kOhm BOM; update the architecture/BOM if retained. |
| J1/J2 sink Rd | Present | R1-R4 are 5.1 kOhm terminations to GND. |
| Test points | Present | TP1-TP15 cover rails, U4 diagnostics, enables, and senses. TP7 reaches U4 ILM. TP8 reaches U4 EN through the R27 bridge. |

## ERC Warnings To Resolve

- Stale `+3V3` labels and wire stubs remain from U14-network placement.
- `EN_HIL`, `HIL_FLT_N`, `VBUS_UP`, `VBUS_PWR`, GND, and `+3.3V` have local/global label duplication.
- U1 has a library-symbol mismatch that must be reconciled after confirming CH334F external-3.3 V mode.

## Manual / Prototype Gates

- Verify U14 reverse current with J1 and J2 alternately powered.
- Validate U4 trip current, thermal latch behavior, and 0-to-3 A load-step sag.
- Confirm CH334F PSELF, supply topology, reset sequence, and OVCUR strapping against the selected CH334F datasheet revision.
- Confirm CP2102N configuration image/readback and GPIO default states.
- Validate USB 2.0 signal integrity on the selected JLCPCB 4-layer stackup using 90 Ohm differential routing, <=0.15 mm skew, and <=2 vias per pair.
- Review exact footprints for J1/J2/J3, U1/U2/U4/U5/U8/U9/U10/U11/U14 before layout; no custom footprint was created in this review.

## Checklist Status

| Area | Status |
|---|---|
| Architecture compliance | Conditional pass; remaining warnings and prototype gates |
| Power path and protection | Conditional pass; TVS and bulk now present, PCB-layout review required |
| U4 eFuse/discharge | Conditional pass; R31 wiring and support network resolved, prototype validation required |
| USB data topology | Conditional pass; PCB routing still manual review |
| CC/Rp behavior | Conditional pass; BOM deviation must be documented |
| Test-point coverage | Pass |
| ERC clean | Pass with 0 errors; warning cleanup remains |