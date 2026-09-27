# HIL USB-C Control

## 1. Product purpose

HIL USB-C Control is a bench device between a Raspberry Pi host and a USB-C HIL device under test (DUT). It gives host software controlled access to the DUT's USB 2.0 data connection, USB-C CC attachment, and 5 V VBUS power.

The product emulates unplugging and reconnecting the DUT without touching its cable. It is intended for controlled laboratory HIL rigs, not as a general-purpose USB-C charger, dock, or power-distribution product.

## 2. Product boundary

### Connectors

| Ref | Role | Required function |
|---|---|---|
| J1 | USB-C upstream / Raspberry Pi | USB 2.0 host data to the board. J1 VBUS may power the board when no external source is present. |
| J2 | USB-C external power input | 5 V external power input. This source is preferred over J1 whenever it is valid. No USB data is connected at J2. |
| J3 | USB-C downstream / DUT | Switched USB 2.0 data, switched CC attachment, and switched 5 V VBUS to the HIL DUT. |

Only USB 2.0 D+ and D- are required. SuperSpeed pairs, DisplayPort, SBU, VCONN, and USB Power Delivery messaging are out of scope.

## 3. Required user-visible behaviour

1. With the board connected to a Raspberry Pi through J1, the Pi enumerates a fixed on-board control interface independently of whether a DUT is connected to J3.
2. Host software can place J3 in either state:
   - **Disconnected:** D+ and D- open, Rp absent, VBUS off and discharged.
   - **Connected:** Rp present, VBUS on, then D+ and D- connected after a software-controlled delay.
3. The control path remains available when J3 is off, disconnected, faulted, or physically unplugged.
4. J2 is the preferred power source. When a valid J2 supply is present, it powers VSYS and J1 is reverse-blocked; J1 must not share the HIL load.
5. When J2 is absent, J1 may power the board and a low-current DUT test. The board still advertises 3 A on J3, so the test controller, not hardware, is responsible for ensuring the DUT load is within the Pi's capability.
6. A downstream short or sustained overload is contained by the J3 eFuse and reported to software.

## 4. Supported operating cases

### A. Pi-powered, low-current DUT

- J1 connects to a Raspberry Pi USB port through a compliant USB-A-to-C or USB-C-to-C connection.
- J2 is not connected.
- The board and DUT are powered from J1.
- Use only for DUT loads that are known to stay within the Pi port's available current, including the board's own consumption.
- This mode is suitable for USB enumeration, data-path testing, and low-power DUT control. It is not a guaranteed 3 A power mode.

### B. External 5 V source on J2, Pi supplies data

- J1 connects to the Pi for USB 2.0 data and control.
- J2 connects to a qualified external 5 V source.
- The priority power mux selects J2; J1 is reverse-blocked and does not provide HIL power.
- This is the normal mode for high-current HIL testing.
- For a DUT expected to draw up to 3 A, qualify the external source, connector, and cable for the **total** load: DUT current plus approximately 0.1 A board consumption. A nominal 3 A USB-C charger does not guarantee a full 3 A remains for the DUT.

### C. Split-cable supply on J1

- J1 carries USB 2.0 data and GND from the Pi, while an external regulated 5 V source drives the board-side J1 VBUS.
- The Pi-side VBUS conductor must be isolated from the external VBUS. The board cannot prevent a split cable that joins an external supply to the Pi's USB port from back-feeding the Pi.
- Treat this as an explicitly qualified fixture configuration, not a general end-user cable mode.
- J2 remains preferred if it is also attached.

## 5. Electrical architecture to implement

### USB and control

- Use a USB 2.0 high-speed hub.
- Keep the control IC on one fixed hub downstream port so the host can always command the board.
- Put J3 on a second hub downstream port through a USB 2.0 high-speed data switch.
- Use a CP2102N GPIO-capable USB interface as the control IC. Its GPIOs drive the HIL VBUS enable, data-switch enable, and CC/Rp enable; they also read HIL fault and VBUS state.
- Hold the hub in reset when J1 VBUS is absent so the upstream Pi does not see a partially powered device.

### Power

- Use a TPS2121-class dual-input priority power mux. Wire J2 as the preferred input and J1 as the auxiliary input.
- The mux must reverse-block both inputs and must not use voltage-based load sharing. J1 must not deliver HIL current while valid J2 power is present.
- Generate +3V3 from VSYS for the hub, control IC, data switch, and CC buffer.
- Keep a TPS259470L-class eFuse only on J3 VBUS. Configure its current limit for approximately 3.59 A typical and below 4.0 A across the selected part's published tolerance; retain output over-voltage protection, fast over-current response, thermal shutdown, fault output, and controlled discharge.
- Retain TVS protection at the USB-C VBUS pins and low-capacitance ESD protection at USB/CC pins.

### CC and Rp

- J1 and J2 are USB-C sinks: provide 5.1 kOhm Rd on both CC pins.
- J3 is a USB-C source: provide fixed 3 A Rp on both CC pins.
- Rp is supplied from +3V3, so use approximately 4.7-4.9 kOhm per CC pin. The nominal implementation is 33 kOhm in parallel with 5.6 kOhm, or a single qualified equivalent resistor.
- Do not implement PD, BC1.2, charger capability sensing, automatic current advertisement selection, or VCONN.

## 6. Software contract

Software owns the state machine. The hardware does not inspect CC for a DUT attach and does not enforce source capability.

### Required disconnect sequence

1. Disable J3 data.
2. Wait at least 20 ms.
3. Disable J3 Rp/CC.
4. Wait at least 20 ms.
5. Disable J3 VBUS.
6. Wait for VBUS discharge confirmation before declaring the DUT disconnected.

### Required connect sequence

1. Enable J3 Rp/CC.
2. Enable J3 VBUS.
3. Confirm VBUS is present and no eFuse fault is active.
4. Wait at least 40 ms.
5. Enable J3 data.

### Required source policy

- Before enabling a high-current DUT test, software must confirm that J2 VBUS is present through a resistor-divider GPIO sense input.
- Software must turn J3 VBUS off before planned J2 removal.
- If J2 presence disappears unexpectedly during a high-current test, software must immediately disable J3 VBUS and record the event. The priority mux may fall back to J1, but J1 is not guaranteed to carry the load.
- On service start, GPIO outputs must be set to the disconnected state before any test is accepted.

## 7. Deliberate shortcuts and accepted limitations

| Shortcut | Reason | Consequence / required mitigation |
|---|---|---|
| Fixed 3 A Rp at J3 | Removes comparator, dynamic Rp switching, and charger-capability detection. | The advertisement can exceed the active source's real capacity. Only enable high-current tests after software confirms qualified J2 power. |
| Software-only DUT attach and disconnect policy | Removes the LM339 attach detector, debounce network, and hardware VBUS interlock. | An unplugged DUT can leave J3 VBUS live until software disables it. Use only in controlled bench fixtures; software must follow the defined sequences. |
| One priority power mux instead of two input eFuses | Removes complex priority, fault, and handover circuitry while retaining deterministic J2 priority and reverse blocking. | No per-input current limit, active 6.3 V over-voltage disconnect, or detailed input fault status. TVS devices are sacrificial protection only. |
| No USB PD or BC1.2 | The product needs 5 V and USB 2.0 only. | It cannot request voltages other than 5 V, negotiate a charger contract, or verify a source's current capability. |
| No SuperSpeed, SBU, VCONN, or DisplayPort | Keeps routing, switching, and validation tractable. | The DUT path is USB 2.0 only. |
| No guarantee of uninterrupted high-current J2-to-J1 failover | J1 may be a Pi port with much less current capacity than the DUT load. | Planned J2 removal requires J3 off; unexpected J2 loss during a high-current test may reset the board or DUT. |

## 8. Explicit non-goals

- Charging arbitrary consumer devices safely from an unknown USB-C charger.
- Supplying a guaranteed 3 A to the DUT from a Raspberry Pi port.
- Operation from an unqualified barrel jack or arbitrary 5 V supply without a validated USB-C fixture.
- USB-C PD source/sink operation, USB-C alternate modes, or USB 3.x traffic.
- Electrical protection against an incorrectly wired split cable that ties external VBUS to the Pi VBUS.

## 9. Acceptance checks before release

1. Verify that J1 carries negligible HIL current while J1 and J2 are both present and J2 is valid, at the maximum DUT load.
2. Verify J2 priority selection and reverse-current blocking across J1/J2 plug-in and removal.
3. Verify the software connect/disconnect sequences cause the intended USB enumeration and loss of enumeration.
4. Verify J3 eFuse operation for overload, short circuit, thermal recovery, VBUS discharge, and fault reporting.
5. Verify Pi-powered low-current operation does not trip the Pi port limiter for the declared low-current DUT set.
6. Verify qualified external-power operation at the intended DUT load, including cable voltage drop and thermal behavior.
7. Verify that unexpected J2 loss causes the software to disable J3 VBUS promptly and leaves the board in a defined safe state.
