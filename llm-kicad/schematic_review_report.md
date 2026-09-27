# USB-C HIL Control — Schematic Review Report (v4)

**Project:** `llm-kicad` (USB-C HIL Control, HIL USB-C bench fixture)
**Scope:** `llm-kicad.kicad_sch` + `01_power_cc`, `02_usb_hub_control`, `03_dut_interface`, `04_test_validation`
**Reviewed against:** `product_description.md`, `architecture/architecture_final.md`, `schematic_review.prompt.md`
**Date:** 2026-09-27 · **Method:** `kicad-schematic-review-skill` (structured extraction + datasheet contract) with 6 parallel subagent workstreams, then independent spot-checks against raw files, vendor datasheets and `kicad-cli` ERC.
**Verification stamp:** every finding below was re-checked against the extracted pin/net facts, the raw `.kicad_sch` values, and a vendor datasheet or the USB Type-C specification. Confidence and evidence are stated per finding.

**Revision history**

| Rev | Date | Change |
|---|---|---|
| v2 | 2026-09-27 | Initial structured review: 5 H, 10 M, 10 L; first fix pass (H1–H5). |
| **v3** | 2026-09-27 (later) | Adds **H6** (crystal pad mapping — a hub-killing defect found after v2). Records **M1** and **M2** as fixed, **M6** and **M7** as accepted (owner decision), **M5** as recommend-accept on analysis, and **M4/M8/M9** as still open. Corrects the net named in **M3** and the `ST` description in **M6**. Adds a new **PCB layout** section (fab blocker, design-rule decisions, current DRC state) and **M11** (C5 value/part mismatch). Refreshes every ERC/DRC figure. Supersedes v2. |
| **v4** | 2026-09-27 (final) | Re-checked from scratch against the saved files after the owner's edit pass. Adds **H7** — the CP2102N GPIO map in the architecture contradicts the schematic, and following it would leave U4 disabled and J3 unable to advertise (the top schematic item). Adds the automated **pin-number vs pad-number audit** (101 footprints, 0 mismatches). Rewrites **M3** (all ERC suppression removed; 4 errors / 2 warnings) and **M11** (C5 is now a value/part/land three-way mismatch). Corrects matrix rows 16 and 22, adds a "Verified at v4" section and a **Where to continue** list. Supersedes v3. |

---

## Verdict

**H1–H6 CLEARED AND VERIFIED — ONE NEW HIGH FINDING IS NOW THE TOP SCHEMATIC ITEM.** Seven high-severity findings have been found in total: H1–H5 (first pass), **H6** (crystal pad mapping — a hub-killing wiring defect) and **H7** — the CP2102N GPIO map in `architecture_final.md` contradicts the schematic on 6 of 7 pins, and following the architecture's own programming instruction would leave **U4 permanently disabled and J3 unable to advertise power to the DUT**. H7 is a contract/documentation defect rather than a wiring defect, but nothing on the board works until it is reconciled.

The design itself is in good shape and measurably better than at v3: a full **pin-number vs pad-number audit across all 101 placed footprints found zero mismatches** (this is the check that would have caught M1 and H6 by machine), **all ERC suppression has been removed** so the ERC result is finally trustworthy, and the USB-C CC/Rd/Rp implementation is fully compliant and needed no change.

**Remaining schematic work is three items:** (1) **H7** — reconcile the GPIO map and generate the CP2102N configuration from the netlist; (2) **M3** — four flags (2 × `PWR_FLAG`, 2 × no-connect) to reach 0 ERC errors; (3) **M11** — C5's value text names 100 µF while its coded part is 47 µF.

| Severity | Count | Status |
|---|---:|---|
| **H** | 7 | H1–H6 **fixed and verified** · **H7 open — top priority** |
| **M** | 11 | M1, M2 **fixed** · M5, M6, M7 **accepted** (M5 on analysis, M6/M7 by owner decision) · M3, M4, M8, M9, M10, M11 **open** |
| **L** | 10 | Open (L1–L10) — documentation/tidiness only |

**Release gates (in order):** (1) **H7** — reconcile the GPIO map, then build `cp2102n_config.hex` from the netlist; (2) **M3** — 2 `PWR_FLAG` + 2 no-connect flags → ERC 0 errors with nothing suppressed; (3) **M11** — fix C5; (4) PCB — fix U4's footprint polygons (the only *fabrication* blocker), route the last ~30 nets, restore the netclass via size to `0.5/0.3`.

---

## Fix log — changes applied 2026-09-27

All changes were applied through Konnect's schematic tools (except where noted) and verified by re-parsing the saved files and re-running ERC.

| Ref | Change | Before → After | Sheet | Finding |
|---|---|---|---|---|
| U1 | Footprint | `QFN-24-1EP_3x4mm_P0.4mm_EP1.65x2.65mm_ThermalVias` → `QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm_ThermalVias` | 02_usb_hub_control | H1 |
| R32 | Value + footprint + LCSC | `15.4k 1%` / `R_0603_1608Metric` / `C2933150` → `24k` / `R_0402_1005Metric` / `C25769` | 01_power_cc | H2 |
| D1 | Value + LCSC | `SMAJ20` / `C115250` → `SMAJ6.5A` / `C123817` | 04_test_validation | H3 |
| D2 | Value + LCSC | `SMAJ20` / `C115250` → `SMAJ6.5A` / `C123817` | 04_test_validation | H3 |
| R28 | Value + LCSC | `100k` / `C25741` → `10k` / `C25744` | 01_power_cc | H4 (applied in KiCad) |
| R33 | LCSC | `C11702` (1 kΩ) → `C25744` (10 kΩ) | 01_power_cc | H5 |
| R34 | LCSC | `C11702` (1 kΩ) → `C25744` (10 kΩ) | 01_power_cc | H5 (applied in KiCad) |
| R35 | LCSC | `C11702` (1 kΩ) → `C25744` (10 kΩ) | 02_usb_hub_control | H5 |

### Fix log — second pass (applied in KiCad by the design owner; verified here by re-export and re-parse)

| Ref | Change | Before → After | Sheet | Finding |
|---|---|---|---|---|
| Y1 | Symbol | `Device:Crystal` (2 pins) → `Device:Crystal_GND24` (4 pins) | 02_usb_hub_control | **H6** |
| Y1 | Value / LCSC | `12MHz X322512MSB4SI` → `Crystal 12MHz`; LCSC `C9002` | 02_usb_hub_control | **H6** |
| Y1 | Footprint | `Crystal_SMD_2520-4Pin_2.5x2.0mm` (tried) → back to `Crystal_SMD_3225-4Pin_3.2x2.5mm` | 02_usb_hub_control | **H6** |
| Y1 | Wiring | pad 2 (`XO`) → pad 3 (`Xtal Out`); pads 2 & 4 → `GND` (previously no-connect) | 02_usb_hub_control | **H6** |
| Y1 | Datasheet | *(empty)* → LCSC CDN PDF for C9002 | 02_usb_hub_control | M7 |
| J2 | Symbol | `USB_C_Receptacle_USB2.0_16P` → `Connector:USB_C_Receptacle_PowerOnly_6P` | 01_power_cc | **M1** |
| C23 | Value / LCSC / footprint | `1 µF` / `C52923` / `C_0402_1005Metric` → `4.7uF 25V` / `C1779` / `C_0805_2012Metric` | 02_usb_hub_control | **M2** |
| C25 | Value / LCSC / footprint | `1 µF` / `C52923` / `C_0402_1005Metric` → `4.7uF 25V` / `C1779` / `C_0805_2012Metric` | 02_usb_hub_control | **M2** |
| C28 | New part | added `100nF` / `C1525` / `C_0402_1005Metric` at U2 VREGIN (the pin previously had no 0.1 µF) | 02_usb_hub_control | **M2** |

### Fix log — third pass (applied in KiCad by the design owner; re-verified here from the saved files)

All four sheets and the project were re-saved at 22:01; this pass records what changed and what it did to the ERC result.

| Ref | Change | Before → After | Sheet | Finding |
|---|---|---|---|---|
| ERC config | `rule_severities` + `erc_exclusions` | four checks at `ignore` and two exclusions → **all removed**; every check back at default severity | project | **M3** |
| U1 | Downstream port usage | ports re-wired so **ports 1 and 2** are populated (`U1.11/12` → CP2102N, `U1.9/10` → U5); ports **3 and 4** are the unused ones | 02_usb_hub_control | **M3** |
| U1 | No-connect flags | port-3 pins (`U1.7/8`) now flagged; **port-4 pins (`U1.5/6`) not yet** | 02_usb_hub_control | **M3** |
| C5 | Value / footprint | `47uF 25V` / `C_1206_3216Metric` → `100uF 10V` / `C_1210_3225Metric` (PCB re-synced to the same 1210 land at 22:17; **LCSC left at `C96123`, which is a 47 µF part** — see M11) | 01_power_cc | **M5/M11** |

**Design-rule changes (project settings, not schematic)**

| Change | Value | Reason |
|---|---|---|
| Via geometry (all 65 placed vias) | `0.45/0.3` → **`0.5/0.3`** (ring 0.075 → **0.100 mm**) | Clears `annular_width` without relaxing any board rule; `min_via_annular_width` stays at 0.1 mm. See the PCB section. |
| `USB_90` netclass clearance | `0.5` → `0.15 mm` | 0.5 mm is geometrically impossible at U2's 0.5 mm pitch. |
| New custom rule `Keep copper pour clear of USB_90 pairs` | `clearance ≥ 0.4 mm` for Zone ↔ `USB_90` | Restores the original 0.5 mm intent for the pour only, without re-breaking the pads. |
| New custom rule `USB_90 intra-class clearance` | `clearance ≥ 0.15 mm` for `USB_90` ↔ `USB_90` | Allows the QFN-28 pad-to-pad spacing. |

**Verification after the fixes**

- Re-parsed all four sheets and the root: every affected part reads back exactly as intended, and no other footprint or value changed.
- Whole-board BOM integrity: every component value now maps to **exactly one** LCSC code, with **0 value/part mismatches** across the 36 parts that carry an LCSC code (the `10k` → C11702/C25744 split is gone).
- `kicad-cli sch erc` re-run after the first pass: **4 messages — 1 error (the pre-existing, excluded GND `power_pin_not_driven`), 3 warnings** — unchanged from the pre-fix run, so no regression and the files remain valid.
- **ERC, current state** (`kicad-cli 10.0.5 sch erc`): **4 messages — 3 errors, 1 warning**: two `pin_not_connected` (U1.11 `DM1−`, U1.12 `DP1+`), one `power_pin_not_driven` on **`VBUS_PWR`** (see M3 — the earlier report named the wrong net), and one `lib_symbol_mismatch` warning for **J1** only (J2's cleared when M1 was fixed). Target after the M3 actions: **0 errors**.
- Diff scope vs. the previous commit: `01_power_cc` 7 lines, `02_usb_hub_control` 2 lines, `04_test_validation` 4 lines — every changed line is accounted for by the table above.

**PCB re-sync — done (was outstanding at v2).** The layout has been re-synced from the schematic and is now heavily routed; all H-fix values and land patterns are present on the board, all 65 vias were re-sized, and the copper pours were re-filled. Current board state is in the **PCB layout** section.

**Process note — KiCad overwrites external edits.** A KiCad project save rewrites every sheet from its in-memory copy. On 2026-09-27 at 13:22:31 a KiCad save silently reverted four edits that had already been written to disk through Konnect while the sheets were open in KiCad (R33 LCSC, R35 LCSC, D1 and D2). Rule going forward: **close the schematic, or quit KiCad, before editing sheets through Konnect** — then reopen — or make the change inside KiCad.

---

## Must-fix findings

### H1 — U1 (CH334F) had the wrong footprint: 3×4 mm / 0.4 mm pitch assigned to a 4×4 mm / 0.5 mm part
**Status: ✅ FIXED 2026-09-27.** U1 now uses `Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm_ThermalVias`, verified against the WCH package drawing §5.5 `QFN24_4×4×0.75-0.5` (body 4.0 ± 0.1 mm, **exposed pad 2.6 ± 0.2 mm**, 0.5 mm pitch, 24 pads + pad 25 = EPAD, which matches the symbol's pin 25 → GND). Two loose ends: the symbol's `ki_fp_filters` still reads `…EP2.7x2.7mm*` (will flag if the footprint-filter ERC check is re-enabled — M3), and the schematic's embedded symbol copy still holds the stale 3×4 mm default, so an "update symbols from library" would revert it.
- **What:** `U1` footprint is `Package_DFN_QFN:QFN-24-1EP_3x4mm_P0.4mm_EP1.65x2.65mm_ThermalVias` (3×4 mm body, 0.4 mm pitch).
- **Why it's wrong:** the WCH CH334/335 datasheet package table specifies **`QFN24_4×4  4*4mm  0.5mm … CH334F`**. The LCSC catalog independently lists C5187527 as **`QFN-24(4x4)`**. A 3×4 mm / 0.4 mm land pattern cannot accept a 4×4 mm / 0.5 mm part — the board would be scrapped.
- **Fix:** assign `Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm_ThermalVias` (present in the installed KiCad library; re-check the EP variant against the WCH land-pattern drawing).
- **Evidence:** WCH CH334/335 V2.91 Table 1-2; JLCPCB cache C5187527 `Package = QFN-24(4x4)`; extraction `02_usb_hub_control` `U1.footprint`. **Confidence: high.**

### H2 — U14 (TPS2121) current-limit resistor R32 = 15.4 kΩ was outside the datasheet range and set ≈6.2 A
**Status: ✅ FIXED 2026-09-27.** `R32 = 24k` / LCSC `C25769` (`0402WGF2402TCE`, 0402, ±1%) → `I_LM = 65.2/24^0.861 ≈ 4.23 A`: above the eFuse's 3.89 A worst case, below the mux's 4.5 A rating, inside the datasheet's 18–100 kΩ R_ILM range.
- **What:** `R32 = 15.4k 1%` on net `U14_ILIM_PENDING` → `U14.10 (ILIM)`.
- **Why it's wrong:** the datasheet recommends **R_ILM = 18 kΩ … 100 kΩ** and publishes the relation `I_LM = 65.2 / R_ILM^0.861` (R in kΩ, I in A). That relation reproduces **all five** published table rows (18.7 kΩ→5.2 A, 22.1→4.5, 29.8→3.5, 44.2→2.5, 80→1.5) and the datasheet's own worked example (59 kΩ → 1.95 A). For **15.4 kΩ it gives ≈6.2 A** — above the device's **4.5 A max continuous input current** and above the **4.2 A VSYS design budget**.
- **Correction to the previous report:** the recorded "15.4 kΩ → 4.23 A" comes from a linear `I = 65.2/R` reading; it does not match the datasheet table.
- **Fix:** `R32 ≈ 24 kΩ` → ≈4.2 A (comfortably above the 3.89 A eFuse limit, below the 4.5 A device rating). Minimum acceptable is 18 kΩ (≈5.4 A). Add the ILIM pin to the lab check list.
- **Evidence:** TPS2121 datasheet §9.3.2 + ILIM table + §10.2.4.3 worked example; extraction `01_power_cc` net `U14_ILIM_PENDING`. **Confidence: high.**

### H3 — D1/D2 (SMAJ20) on J1/J2 VBUS could not protect the 5 V inputs at all
**Status: ✅ FIXED 2026-09-27.** D1 and D2 are now `SMAJ6.5A` / LCSC `C123817`, matching D3 — 6.5 V stand-off (safely above the 5.5 V VBUS maximum) with breakdown from ≈7.2 V. Remaining caveat, unchanged by the fix: this is a **sacrificial clamp** — it limits the excursion rather than guaranteeing the mux's ≈6 V limit. Guaranteed protection would require using the mux's OV1/OV2 pins (currently tied to GND), which changes fall-back behaviour and is not currently in the product contract.
- **What:** `D1 = SMAJ20` on `VBUS_UP` (J1) and `D2 = SMAJ20` on `VBUS_PWR` (J2).
- **Why it's wrong:** SMAJ20 is a **20 V stand-off** device (V_BR 22.2–24.5 V, clamp ≈32 V at 12.3 A). It does not conduct below ~22 V, while the TPS2121's IN1/IN2 inputs and the CP2 control pin are **≈6 V absolute maximum** (control-pin operating range 0–5.5 V). A USB-C hot-plug transient (cable ringing routinely reaches 8–10 V) therefore reaches the mux unclamped. The stated intent — "TVS devices handle ESD and short hot-plug transients" — is not achieved by this part.
- **Coupled risk:** `CP2 (pin 3)` is tied **directly** to J2 VBUS, leaving only 0.5 V of margin to the 6 V absolute maximum; the datasheet's comparator configurations use a divider on this pin.
- **What is correct:** `D3 = SMAJ6.5A` on `VBUS_HIL` is the right class of device for a 5 V rail and is correctly coordinated with U4's 6.19 V OVLO trip (the OVLO acts first; the TVS handles the faster/larger event).
- **Fix:** use a 5.0–6.5 V stand-off TVS on J1 and J2 VBUS (SMAJ5.0A / SMAJ6.0A / SMAJ6.5A) and verify the clamp stays below the mux's absolute maximum at the expected surge current. Consider a divider on CP2 for extra margin.
- **Evidence:** extraction values `D1/D2 = SMAJ20`, `D3 = SMAJ6.5A`; TPS2121 Section 6 + Table 7.1/7.3. **Confidence: high** (part selections verified; the clamp/stand-off relationship is standard TVS data).

### H4 — U4 enable default was indeterminate: R28 = 100 kΩ was too weak (VBUS could be live on J3 at power-up)
**Status: ✅ FIXED 2026-09-27.** `R28 = 10k` / LCSC `C25744` (applied inside KiCad — see M10 for why Konnect refused the symbol) → EN_HIL ≈0.3 V worst case with the CP2102N's 10–30 µA weak pull-up, comfortably below the TPS259470L's 1.183 V minimum rising threshold. The eFuse can no longer enable at power-up before software writes its defaults.
- **What:** `HIL_VBUS_EN` = `U2.22 (GPIO.4)` → `R27 = 1 kΩ` → `EN_HIL` → `U4.1 (EN/UVLO)`, with `R28 = 100 kΩ` to GND.
- **Why it's wrong:** the CP2102N's documented default is *"open-drain with a weak pull-up enabled and the port latch set to 1"* (I_PU = 10–30 µA) **during reset and before configuration**. Through R27+R28 that sources 10–30 µA into ≈101 kΩ → **EN_HIL ≈ 1.0–3.0 V**. The TPS259470L's EN/UVLO rising threshold is **1.183 / 1.20 / 1.223 V** (min/typ/max), so the node straddles the threshold: **U4 can enable at power-up and put VBUS on J3 before the service writes its defaults**, with the discharge FET off in that state.
- **Requirements violated:** architecture §1 "Deterministic default … U4 EN low and discharge on"; product §6 "On service start, GPIO outputs must be set to the disconnected state before any test is accepted".
- **The datasheet prescribes the fix:** "a strong pull-down (10 kΩ) can be used to ensure the pin remains low during reset". R28 is 10× too weak.
- **Fix:** `R28 = 10 kΩ` (keep R27 as the series element) → EN_HIL ≈ 0.3 V worst case.
- **What is correctly done:** `HIL_DATA_EN_N` and `HIL_CC_EN_N` are active-low, so their power-up default (high) *is* the safe state, and `R48`/`R49` = 100 kΩ provide explicit pull-ups. Only the inverted-logic enable needs the strong pull-down.
- **Evidence:** CP2102N §4.3.1 + Table 3.7 (I_PU 10–30 µA); TPS259470x Table 6-1 (V_UVLO(R) 1.183/1.20/1.223 V); extraction `R27 = 1k`, `R28 = 100k`. **Confidence: medium-high** — the outcome depends on the un-tightened weak pull-up value, which is precisely why the default cannot be guaranteed as designed.

---

### H5 — Three 10 kΩ resistors carried a 1 kΩ part number (the wrong part would have been fitted)
**Status: ✅ FIXED 2026-09-27.**
- **What:** `R33`, `R34` and `R35` all had `Value = 10k` but `LCSC = C11702`, which is **`0402WGF1001TCE` = 1 kΩ**. The 10 kΩ part is `0402WGF1002TCE` = C25744 — the two MPNs differ by a single digit, which is almost certainly how they were crossed.
- **Why it matters:** JLCPCB builds from the LCSC codes in the BOM, so all three would have been fitted as 1 kΩ, silently changing the design.
- **Impact by part:** `R33` is the TPS2121 `ST` pull-up — the datasheet specifies **R_ST = 6–20 kΩ**, so 1 kΩ is outside spec; `R34` is the `HIL_FLT_N` pull-up, where 1 kΩ instead of 10 kΩ raises the open-drain sink current from 0.33 mA to 3.3 mA (≈4.8 mA with LED3's path on the same node) — TI does not state the FLT pin's sink capability; `R35` (CH334F `~OVCUR#` pull-up) is insensitive to the change.
- **How it was found:** a whole-board cross-check of every `LCSC` property against its catalog value, run while applying the H2 fix — not visible to ERC or DRC.
- **Fix applied:** R33, R34 and R35 now carry `C25744` (10 kΩ ±1%, 0402, JLCPCB Basic).
- **Evidence:** JLCPCB catalog rows for C11702 (`0402WGF1001TCE`, 1 kΩ) and C25744 (`0402WGF1002TCE`, 10 kΩ); TPS2121 §10.2.3 and Table 7.3 (R_ST 6–20 kΩ). **Confidence: high.**

---

### H6 — Y1's 4-pad crystal was wired to a ground pad instead of XOUT (the hub could never have clocked)

**Status: ✅ FIXED 2026-09-27 (second pass).** Y1 now uses `Device:Crystal_GND24`, wired pin 1 → `Net-(U1-XI)`, pin 3 → `Net-(U1-XO)`, pins 2 and 4 → `GND`. Re-verified from the exported netlist: `Net-(U1-XI) = U1.4 + Y1.1`, `Net-(U1-XO) = U1.3 + Y1.3`, and `Y1.2`/`Y1.4` are on the `GND` net.
- **What:** `Y1` used the two-pin symbol `Device:Crystal` on the four-pad footprint `Crystal_SMD_3225-4Pin_3.2x2.5mm`. KiCad maps symbol pin *n* → footprint pad *n*, so the board connected `XI` → pad 1 and `XO` → pad 2, leaving pads 3 and 4 with **no net at all** (read directly out of the `.kicad_pcb` pad blocks).
- **Why it's wrong:** the four-pad SMD crystal pinout is **crystal terminals on pads 1 and 3, case/GND on pads 2 and 4**. Pad 2 is a *ground* pad and pad 3 — the real `XOUT` — was floating. The CH334F's oscillator was therefore connected between `XI` and a ground pad with `XOUT` open: **it would never have started, so the hub — and every USB path on the board — would have been dead.** ERC and DRC cannot see this; the netlist is syntactically valid.
- **Evidence (three independent textual sources, none of them a rendered drawing):**
  - **Lucki `L225S120U11L`** (SMD2520-4P) datasheet pin table: `#1 Xtal Terminal (Input)`, `#2 GND`, `#3 Xtal Terminal (Output)`, `#4 GND`.
  - **ECS `ECX-1048`** datasheet "PAD CONNECTIONS": `1 In/Out`, `2 NC`, `3 Out/In`, `4 Gnd` — same terminal pairing (1 and 3).
  - **KiCad's own library:** `Device:Crystal_GND24` is described as *"Four pin crystal, GND on pins 2 and 4"*, and the generic `Crystal_SMD_*4Pin*` footprints all share the identical quadrant pad numbering.
  - The TXC `7M` drawing that `Crystal_SMD_3225-4Pin_3.2x2.5mm` is traced from (URL in the footprint's own description field) shows the same arrangement.
- **Note on method:** an automated read of the *rendered* YXC drawing suggested pads 3 and 4 were ground, conflicting with all three textual sources; the vector drawing has no extractable text layer, so the textual pin tables were treated as authoritative. **Confidence: high.**
- **Fix:** `Device:Crystal_GND24`, pin 1 → XI, pin 3 → XO, pins 2 & 4 → GND. **Generalise the rule: a two-pin crystal symbol on a four-pad footprint is a latent wiring error** — it looks correct in the schematic and produces a valid netlist.
- **Secondary benefit:** grounding pads 2 and 4 also shields the case, which the floating version did not.
- **Related:** the three dead `Datasheet` fields (M7) and C5's value/part mismatch (M11) are siblings of this class — an attribute that is not checked by any automated tool.

---

### H7 — The architecture's GPIO map contradicts the schematic on 6 of 7 pins; following it would leave U4 disabled and J3 unable to advertise

**Status: ❌ OPEN — top priority. Reconcile the documents, then generate the CP2102N configuration from the netlist. No board or schematic change is needed.**

This is a *contract* defect rather than a wiring defect, but its consequence is a fixture that does not work — and it is invisible to ERC and DRC, because no automated check compares a document to a netlist.

- **What:** the CP2102N's actual pin allocation, read from the exported netlist's `pinfunction` values and their nets:

| CP2102N pin | Pin function | Net in the schematic | `architecture_final.md` §6 says |
|---:|---|---|---|
| 19 | `~TXT`/GPIO.0 | `HIL_FLT_N` | `HIL_FLT_N` ✅ |
| 18 | `~RXT`/GPIO.1 | `VBUS_HIL_SNS` | `VBUS_PWR_SNS` ✗ |
| 17 | `RS485`/GPIO.2 | `HIL_CC_EN_N` | `VBUS_HIL_SNS` ✗ |
| 16 | `~WAKEUP`/GPIO.3 | `VBUS_PWR_SNS` | `VBUS_UP_SNS` ✗ |
| 22 | GPIO.4 | `HIL_DATA_EN_N` | `HIL_VBUS_EN` ✗ |
| 21 | GPIO.5 | `VBUS_UP_SNS` | `HIL_DATA_EN_N` ✗ |
| 20 | GPIO.6 | `HIL_VBUS_EN` | `HIL_CC_EN_N` ✗ |

- **Why it matters:** `architecture_final.md` §6 — echoed by the release-gate instruction to *"Program CP2102N with Silicon Labs Xpress Configurator … GPIO.4 push-pull reset low; GPIO.5/6 open-drain reset high; GPIO.0-3 digital inputs"* and to ship a `cp2102n_config.hex` generated from that manifest — tells the programmer to make **GPIO.0–3 inputs, GPIO.4 a push-pull output and GPIO.5/6 open-drain outputs**. Applied to *this* hardware, three pins receive a functionally wrong mode:
  - **pin 20 (`GPIO.6`) = `HIL_VBUS_EN`, configured open-drain.** `HIL_VBUS_EN` reaches `EN_HIL` through `R27` (1 kΩ) with `R28` (10 kΩ) to GND, so enabling U4 requires the pin to **drive high** past the TPS259470L's 1.20 V UVLO threshold. An open-drain output cannot drive high, so `EN_HIL` stays at ≈0 V and **U4 never enables — no DUT power is ever delivered.** The push-pull assignment lands instead on pin 22 (`HIL_DATA_EN_N`), where it is harmless.
  - **pin 17 (`GPIO.2`) = `HIL_CC_EN_N`, configured as an input.** As an input it cannot drive, so `R49` (100 kΩ) holds U6's `!OE` high → U6's output is high-Z → `VRP_3A` floats → **no Rp reaches `J3_CC1`/`J3_CC2`, so J3 never advertises power and the DUT never comes up.**
  - **pin 21 (`GPIO.5`) = `VBUS_UP_SNS`, configured as an open-drain output.** That pin reads the `R73`/`R74` 100 kΩ divider, so driving it as an output clamps or corrupts the J1-present sense.
- **Which side is wrong: the schematic is right and the architecture is wrong.** The wiring is self-consistent with the intent — the two pins that must be open-drain outputs (`HIL_DATA_EN_N`, `HIL_CC_EN_N`) each carry a 100 kΩ pull-up (`R48`, `R49`), the pin that must be push-pull (`HIL_VBUS_EN`) drives a series-resistor/threshold path with no pull-up, and the four sense nets drive dividers into the chip. Only the *assignments* differ from §6.
- **Fix:** correct the §6 table, the `cp2102n_config.hex` instruction and the `schematic_overview.md` block diagram to the map above, then derive the configuration image from the **netlist** rather than from the document. Nothing needs to change on the schematic or the board.
- **Why it was missed until now:** the earlier reviews verified that the architecture's GPIO table was internally consistent and that each net was wired as its name implies — which is exactly what hides a pin-level permutation. Both the document and the net names are self-consistent; only their *pairing* is wrong.
- **Evidence:** netlist `pinfunction` values for U2 with their net membership (exported 2026-09-27); `architecture/architecture_final.md` §6 and the release-gate instruction; `architecture/schematic_overview.md`; net membership of `R27`, `R28`, `R48`, `R49`, `R73`, `R74`; U6 (`SN74LVC1G125`) pin 1 = `!OE`. **Confidence: high** for the mismatch and for all three mode consequences.

---

## Medium findings

### M1 — J2 mixed a 16-pin USB-C symbol with a 6-pin footprint (silent pin/pad mismatch)
**Status: ✅ FIXED 2026-09-27.** J2 now uses `Connector:USB_C_Receptacle_PowerOnly_6P`, whose seven pins (`A5`, `B5`, `A9`, `B9`, `A12`, `B12`, `SH`) map one-to-one onto the C283540 footprint. Verified from the exported netlist: J2's VBUS pins sit on `VBUS_PWR`, its CC pins on `J2_CC1`/`J2_CC2`, and `A12`/`B12`/`SH` on `GND` — no phantom pins, nothing left dangling. ERC's `lib_symbol_mismatch` warning for J2 is gone (only J1's remains).
- **What it was:** `J2` used `Connector:USB_C_Receptacle_USB2.0_16P` (pins A1, A4, A5, A6–A9, B1, B4, B5, B6–B9, A12, B12, SH) with footprint `Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-17`, whose pads are only **A5, B5, A9, B9, A12, B12, SH**. Symbol pins **A1/A4/B1/B4 had no pad** → silently unconnected. It happened to work (every *connected* pin mapped), but the mismatch was invisible because ERC's "Assigned footprint doesn't match footprint filters" check is **globally disabled** (M3).
- **Consequence that had to be fixed alongside it:** the old 16-pin symbol carried a `power_out` pin on VBUS. The new 6-pin symbol has **passive** VBUS pins, so `VBUS_PWR` lost its driver and now raises a new `power_pin_not_driven` error — see M3. That is expected, and a `PWR_FLAG` on `VBUS_PWR` closes it.
- **Evidence:** pad list read from `Connector_USB.pretty/USB_C_Receptacle_HRO_TYPE-C-31-M-17.kicad_mod`; current netlist export; current ERC report. **Confidence: high.**

### M2 — CP2102N decoupling was below the datasheet requirement
**Status: ✅ FIXED 2026-09-27 (values). ⚠️ Placement still open.** The CP2102N note — *"4.7 µF and 0.1 µF bypass capacitors required for each power pin **placed as close to the pins as possible**"* — appears on Figures 2.1, 2.2 **and** 2.3. This design is the Figure 2.3 arrangement (internal regulator unused: VDD and VREGIN both on the 3.3 V rail).

| Pin | Requires | Now | |
|---|---|---|---|
| VDD (6) | 4.7 µF + 0.1 µF | C23 = 4.7 µF **C1779** (0805) + C24 = 100 nF (0402) | ✅ |
| VREGIN (7) | 4.7 µF + 0.1 µF | C25 = 4.7 µF **C1779** (0805) + C28 = 100 nF (0402, newly added) | ✅ |

- **What it was:** `C23 = 1 µF` + `C24 = 100 nF` on VDD; `C25 = 1 µF` on VREGIN with **no** 0.1 µF at all.
- **Why the chosen part is the better outcome:** the 4.7 µF part is `C1779` — **JLCPCB Basic**, already used at C1/C3 — and 0805 rather than a smaller 0603. That gives one unique 4.7 µF line in the BOM instead of two, **no extended-part setup fee**, and less DC-bias derating than a 0603 X5R 4.7 µF/25 V at 5 V. The 0.1 µF caps stay 0402, which is correct for HF decoupling.
- **Still open — placement.** Measured on the PCB, all four caps sat **5.9–6.6 mm** from U2 pads 6/7 (C24 5.88 mm, C28 6.58 mm at the last measurement), against a ≤3 mm target for the 100 nF parts. U2 is a 135°-rotated QFN-28 whose power pins face a region occupied by the R70–R75 dividers and TP10/TP15, so the caps ended up on the far side of the package. **Re-verify after the current placement pass** — the area immediately outward of pins 6/7 (≈160.5, 110.5) is where the two 100 nF parts belong, with the bulk within ~5 mm.
- **Evidence:** CP2102N §2.1 + Figures 2.1–2.3 (note text confirmed identical on all three); JLCPCB catalogue rows for C1779/C52923/C1525; PCB pad-coordinate measurement. **Confidence: high** (requirement and values); **medium** (placement, being a layout property).

### M3 — ERC status: all suppression removed; 4 errors and 2 warnings remain
**Status: ❌ OPEN — four flags away from clean.** Substantially improved since v3: the project's `schematic.erc.rule_severities` and `erc_exclusions` are now **empty**, i.e. every check is back at its default severity and **nothing is suppressed**. The ERC result is finally trustworthy — which is why the count changed, not because the design got worse.
- **Current ERC** (`kicad-cli 10.0.5 sch erc --severity-all`) → **6 violations — 4 errors, 2 warnings**:

| Violation | Objects | Fix |
|---|---|---|
| `power_pin_not_driven` | `#PWR002` = `power:GND`, `04_test_validation` @ (30.48, 74.93) | `PWR_FLAG` on GND |
| `power_pin_not_driven` | `#PWR004` = a `power:VSS` whose *Value* was renamed to **`VBUS_PWR`**, `04_test_validation` @ (82.55, 29.21) | `PWR_FLAG` on `VBUS_PWR` |
| `pin_not_connected` ×2 | **U1 pins 5 (`DM4-`) and 6 (`DP4+`)** @ (160.02, 53.34 / 55.88) | no-connect flags |
| `lib_symbol_mismatch` ×2 | **J1** (`Connector:USB_C_Receptacle_USB2.0_16P`) and **U1** (`Interface_USB:CH334F`) cached symbols differ from the library copies | update symbols from library, or document |

- **The hub now uses ports 1 and 2** (`U1.11/12` → CP2102N, `U1.9/10` → U5) and leaves **ports 3 and 4** unused. Port 3's pins (`U1.7/U1.8`) already carry no-connect flags; port 4's (`U1.5/U1.6`) do not — that is the source of the two `pin_not_connected` errors. In the v3 snapshot the unconnected pair was recorded as port 1; the design changed since.
- **`VBUS_PWR`'s error is a direct consequence of the M1 fix:** the old 16-pin J2 symbol carried a `power_out` pin on VBUS, while the 6-pin replacement has passive pins, so the rail lost its driver. A `PWR_FLAG` is the right answer — not a symbol change.
- **Secondary tidy:** a `power:VSS` (ground) graphic carrying the name of a 5 V rail is misleading to any reader. Replace it with a `PWR_FLAG` plus a net label.
- **The warning worth keeping:** U1's `lib_symbol_mismatch` exists because the cached symbol was edited in place (the H1 footprint fix). A careless "update symbols from library" would revert it, so decide deliberately — update the library symbol to match, or keep the cached one and record why.
- **Target state: ERC = 0 errors, 0 suppressed checks, and any remaining warning documented.**
- **Evidence:** fresh `kicad-cli` ERC report (2026-09-27T22:08); `.kicad_pro` `schematic.erc` read as an **empty object**; `#PWR002`/`#PWR004` identity, value and coordinates read from `04_test_validation.kicad_sch`; U1 pin numbers from the netlist. **Confidence: high.**

### M4 — U4 EN/UVLO has no supply-side UVLO divider
**Status: ❌ OPEN — awaiting owner decision** (add a VSYS UVLO divider, or accept and document). No change made.
- EN/UVLO is driven directly from a 3.3 V GPIO, so the eFuse's own UVLO comparator is bypassed. Whenever EN is high the part passes whatever VSYS happens to be (e.g. a sagging Pi port) to J3 as "VBUS", below the USB-C 4.75 V minimum.
- **Fix:** optional VSYS divider sized for ~4.5–4.6 V UVLO (the datasheet permits pulling EN to IN below 5 V — the missing element is the UVLO point itself), or accept and document.
- **Evidence:** TPS259470x Table 6-1 and §6.3 note 2; extraction `EN_HIL` net. **Confidence: high** (mechanism); the severity is a policy call.

### M5 — U14 output bulk capacitance (C5 = 47 µF) is below the datasheet example
**Status: ⚠️ RECOMMEND ACCEPT — analysis complete, owner decision pending.** The datasheet's switchover examples use `C_L = 100 µF`, but that is an example used to bound output droop, not a requirement, and the numbers do not motivate a change.
- **Fast switchover (5 µs, `CP2 ≥ VREF` — the normal case, J2 present):** ΔV = I·t/C = 3.89 A × 5 µs ÷ 47 µF = **0.41 V** → VSYS dips 5.0 → **4.6 V**. The TLV76733 needs 3.3 V + dropout (≈0.3–0.5 V) ≈ **3.6–3.8 V minimum**, so 4.6 V clears it with ~0.8–1.0 V of margin. 100 µF would give 0.19 V instead.
- **Slow switchover (100 µs, J2 absent):** at the full 3.89 A the output collapses regardless of any practical capacitor — 94 µF still gives 4.14 V of droop. **No amount of bulk in this range fixes it**, so adding capacitance buys nothing for the one scenario that carries risk. That scenario is already handled by design intent: `product_description` requires **software to turn J3 VBUS off before a planned J2 removal** (load then ≈115 mA → droop ≈0.24 mV), and the *unplanned* full-load J2 loss is an explicitly accepted limitation ("may reset the board or DUT").
- **If it were to be changed:** 100 µF is available in the **same 1206 land** (`C883598` = `GRM31CR61A107MEA8L`, 1206, 100 µF, 10 V, Extended) with zero layout change, or in 1210 (+56 % area). Do **not** use a 6.3 V part on this rail: VSYS can transiently see the TVS clamp (breakdown ≥7.2 V), so ≥10 V, preferably 16 V.
- **How to close it:** accept on analysis, or convert it to a measurement — trigger on `VBUS_PWR_SNS` falling, capture VSYS during a J2→J1 handover at load, and confirm the dip stays above ≈3.8 V.
- **Evidence:** TPS2121 §9.3.7 Eq. 3–4 and §10.2.2/§10.3.2; TLV76733 dropout from the TLV767 datasheet; `product_description` J2-removal procedure and accepted-limitations entries. **Confidence: high** (analysis); bench confirmation still recommended.

### M6 — U14 `ST` (source status) is not observable
**Status: ✅ ACCEPTED (owner decision: "will not change; no need to observe").** No schematic change; the accepted limitation is recorded here.
- **What `ST` is:** the TPS2121 datasheet describes pin 9 as *"Status output indicating which channel is selected. Connect to GND if not required."* The behaviour is explicit — **`ST` is pulled high when the output is Hi-Z or IN1, and pulled low when IN2 is powering the output** — so on this board **`ST` low = J2 is feeding VSYS; `ST` high = J1, or VSYS is Hi-Z**. It is an open-drain output (the symbol's pin type is `open_collector`, and "connect to GND if not required" is only a legal instruction for an open-drain output). `R33 = 10 kΩ` sits inside the datasheet's **R_ST = 6–20 kΩ** range — the H5 fix brought it there from an out-of-spec 1 kΩ.
- **Correction to v2:** the earlier text speculated that `ST` might be push-pull and leak ~170 µA into the 3.3 V rail. That is wrong — the datasheet's "connect to GND if not required" wording establishes open-drain, consistent with the symbol. The only real coupling is R33 sinking 0.33 mA from the 3.3 V rail while `ST` is low.
- **Current wiring:** `U14_ST` = `U14.9` + `R33.2` only. No test point and no GPIO. Because `R33` is fitted, the net is a live probe point at R33's pad — it is not literally inaccessible, just not designed for test.
- **Consequence accepted:** acceptance check #2 ("verify J2 priority selection and reverse-current blocking across J1/J2 plug-in and removal") cannot be read off a status bit. Software can still infer J2 *presence* from `VBUS_PWR_SNS` (R70/R71/R72 → CP2102N GPIO.1) — but that divider cannot prove the mux actually *switched*, which only `ST` can.
- **Two documentation follow-ons this acceptance depends on:**
  1. `architecture_final.md` promises a *"GPIO.7/DNP test pad"* on U14_ST, but the CP2102N has **no GPIO.7** (Table 4.2 lists GPIO.0–GPIO.5). As written the document describes an observability feature that does not exist — correct it.
  2. Reword acceptance check #2 as a *behavioural* test: remove J2 and confirm VSYS falls to the J1 level while `VBUS_PWR_SNS` reports J2's presence.
- **Evidence:** TPS2121 §6, §9.5 Table 9-3 and §10.2.3 (R_ST); CP2102N Table 4.2; netlist export. **Confidence: high.**

### M7 — Datasheet properties: three dead links; all others not tracked (owner decision)
**Status: ✅ ACCEPTED (owner decision: "links are not needed").** Nothing in the EDA flow or at JLCPCB consumes the `Datasheet` field — assembly runs off the LCSC codes — so datasheets are tracked outside the schematic. Recorded here so the decision is explicit rather than looking like an omission.
- **Audit (current):** 101 components — **18 with a `Datasheet` value, 83 empty**. `Y1` now has one (added during H6: the LCSC CDN PDF for C9002).
- **Three fields are worse than empty** — they look like links and resolve to nothing. Recommend clearing them so "not tracked here" is unambiguous:

| Ref | Value | Problem |
|---|---|---|
| `U8` | `www.ti.com/lit/gpn/TLV767` | no scheme — will not resolve |
| `U14` | `data_cache/TPS2121RUXR_TI_datasheet.pdf` | **`data_cache/` does not exist** in the project |
| `U4` | `data_cache/TPS259470LRPWR_LCSC_datasheet.pdf` | same |

- **Note:** U4, U14 and U8 are **not** among the M10 stale-instance symbols, so Konnect can edit them if the tidy is wanted.
- **Evidence:** `Datasheet` property audit across all four sheets; directory listing confirming `data_cache/` is absent. **Confidence: high.**

### M8 — Unused TS3USB221A port pins left open
**Status: ❌ OPEN — awaiting owner decision** (add the 50 Ω terminations, or document that the port is unreachable). No change made.
- TI recommends connecting unused switch pins to ground through **50 Ω** to prevent reflections; `U5.3/U5.4 (2D+/2D-)` are no-connect.
- **Fix:** 50 Ω to GND on each, or document that no signal can ever reach that port (SEL is strapped to GND, so the 2D port is never selected).
- **Evidence:** TS3USB221A §7.3.1/§8.2.2; extraction `U5` pins + NC list. **Confidence: high.**

### M9 — eFuse latch-off recovery must be an explicit software path
**Status: ❌ OPEN — documentation/contract item.** The schematic is already consistent with the architecture; what is missing is the written recovery sequence. No code or schematic change required.
- U4 is the **-L (latch-off)** variant: after thermal shutdown it stays off until VIN is cycled below V_UVP(R) or EN/UVLO is toggled below V_SD(F) (Table 7-2). With **ITIMER open** (documented valid), a persistent overcurrent is *limited* rather than latched, so a sustained short maintains current limit and eventually relies on thermal shutdown + latch-off.
- **Fix:** define the recovery sequence in the software contract — drive EN_HIL low for a defined interval in `hil_off()`, and have software treat a latched FAULT as requiring that reset. The schematic is consistent with the architecture; this is a contract/documentation item.
- **Evidence:** TPS259470x Table 7-2, §7.3.5.3 Note 1, §4 device comparison. **Confidence: high.**

---

### M10 — 43 symbols carry a stale duplicate `instances` record (blocks tooling; no design impact)
**Status: ❌ OPEN — tooling hygiene only, no design impact** (44 at v2, 43 now — one cleared when J2's symbol was replaced). Nothing in the netlist, ERC, DRC or fabrication is affected; it is recorded because it blocks scripted edits to the major parts.
- **What:** each sub-sheet carries a second `instances` record naming the sheet *file* as the project — e.g. `(project "01_power_cc" (path "/5ebe429a-…" (page "1")))` alongside the correct `(project "llm-kicad" (path "/359235dd-…/63913550-…" (page "2")))`. KiCad writes this when a `.kicad_sch` is opened standalone instead of as part of the project.
- **Scope (re-counted at v3):** **43** symbols (was 44 — one cleared when J2's symbol was replaced) — `01_power_cc` 12 (R27, C10, R28, C9, R30, R31, C11, C13, C12, R34, C14, R29); `02_usb_hub_control` 20 (including U1, U2, J1); `03_dut_interface` 9 (including U5, U6, J3, U10); `04_test_validation` 2.
- **Impact:** none on the netlist, ERC, DRC or fabrication — KiCad resolves the record matching the open project. But Konnect's edit tools **refuse** any of those symbols with `ambiguous_target` (demonstrated on R28 and R34, which had to be corrected inside KiCad). Any future tooling that resolves symbols through the instance records will be blocked on U1, U2, U5, U6, U9, U10, J1 and J3 — all major parts.
- **Fix:** remove the stale `(project "<sheetfile>" …)` records. This is a structural file edit that needs an explicit decision (Konnect has no tool for it); the alternative is to accept it and edit those parts in KiCad's UI.
- **Evidence:** S-expression parse of all four sheets; two `ambiguous_target` refusals returned by `edit_schematic_component`. **Confidence: high.**

---

### M11 — C5's value text does not match its coded part (H5-class mismatch, still open)
**Status: ❌ OPEN — one field.** The v3 form of this finding (value text claiming 25 V against a 10 V part) was partly addressed, but the edit introduced a new inconsistency in the other direction.
- **What:** `C5` now has `Value = 100uF 10V`, `LCSC = C96123` = `CL31A476MPHNNNE` = **10 V *47 µF* X5R on a *1206* body**, and `Footprint = Capacitor_SMD:C_1210_3225Metric`. The board was re-synced to the same 1210 land at 22:17, so **schematic and PCB now agree (0 footprint mismatches of 101)**. What remains is that the **value overstates the capacitance by 2×**, and the coded part is a 1206 body on a 1210 land.
- **Why it matters:** same class as **H5** — schematic text that contradicts the BOM — but here the risk is specific. The M5 analysis is *about* whether 47 µF is sufficient on VSYS, so a reader seeing "100uF" would conclude the bulk-capacitance question has been closed when it has not.
- **Fix — one field either way:**
  - **Commit to 100 µF (keeps the 1210 land; smallest change):** set `LCSC = C23742` (`CL32A107MPVNNNE`, 1210, 100 µF, 10 V, **Extended**, $0.445) — or `C90143` / `C7432790` for **16 V** margin against the TVS clamp at $0.92 / $0.43. The value text and the land already match; only the part code changes.
  - **Revert to 47 µF (cheapest part, but needs a re-sync):** set `Value = 47uF 10V`, keep `C96123` (**JLCPCB Basic**, $0.243, 338 k stock), and change the land back to `Capacitor_SMD:C_1206_3216Metric` so it matches the 1206 body.
- **Why there is no "100 µF *and* Basic" option:** the catalogue contains only **two** Basic 100 µF parts in total — `C15008` (`CL31A107MQHNNNE`, 1206, **6.3 V** X5R, $0.125, 2.06 M stock, the only one in a 1206/1210 body — 1 of 102 such parts) and `C16133` (a CASE-B tantalum, also 6.3 V). **Zero** Basic 100 µF parts exist at 10 V or above. Committing to 100 µF therefore necessarily costs one Extended-part fee; staying Basic means staying at 47 µF with `C96123`. `C15008` is not usable on this rail: VSYS has OVLO at 6.19 V and a TVS (SMAJ6.5A) that stands off at 6.5 V, so a 6.3 V part would sit at ≈98 % of its rating before protection acts.
- **Cross-reference M5:** the analysis showed 100 µF buys little — the 5 µs switchover droop is 0.41 V at 47 µF (VSYS 4.60 V, comfortably above the TLV76733's ≈3.6–3.8 V minimum) and the risky 100 µs case collapses at any practical capacitance. Either choice is defensible; what is not defensible is a value that names a capacitance the BOM does not contain.
- **Why it was missed, and how it is caught now:** the v2/v3 whole-board cross-check compared a voltage rating embedded in the value text but not the capacitance. A check that compares **every numeric token** in the value against the catalogue description now exists as `tools/audit_schematic.py` — it flags exactly this defect, and reports C5 as its only hit.
- **Evidence:** JLCPCB catalogue rows for C96123 (`10V 47uF X5R ±20%`, 1206, Basic), C883598, C23742, C90143, C7432790; extraction of `01_power_cc` `C5`; the placed PCB footprint instance for C5 (board re-saved 22:17). **Confidence: high.**

---

## Low findings / considerations

- **L1 — Rail naming:** the 3.3 V rail is `+3.3V` on all 25 power symbols; the architecture, BOM and Konnect conventions say `+3V3`. Internally consistent, but rename for documentation/tooling consistency (no electrical impact).
- **L2 — Misleading net names:** `U14_ILIM_PENDING` and `U14_SS_PENDING` persist although R32 and C6 are fitted. Rename to `U14_ILIM` / `U14_SS` — and see H2, the name is an honest hint that the value was never closed.
- **L3 — BOM MPN inconsistency:** `U11 = TPD4E05U06DQAR` while `U9/U10 = TPD4E05U06DQA` (same die, reel suffix). Unify the BOM part number.
- **L4 — Footprint library name vs variant:** U4's footprint library is `TPS259470ARPWR` while the part is `TPS259470LRPWR`. Same RPW0010A package, so geometry is fine, but the **A and L variants differ in fault behaviour** — rename to prevent an AVL mix-up.
- **L5 — Rp tolerance not specified:** `R5/R6 = 4.7k` with no tolerance recorded. USB-C Table 4-24's 3.3 V column is 4.7 kΩ ±5%; specify 1% parts for margin and record it in the BOM. (Verified safe even at ±5% with a ±10% sink Rd.)
- **L6 — Non-contiguous reference designators:** no U3/U7/U12/U13, no C2/C4/C7/C8/C15, no R7–R26, R41/R42, R53–R69. Re-annotate before release for BOM/AVL clarity.
- **L7 — Test point gap:** TP1–TP15 cover the rails, both VBUS senses, EN_HIL, HIL_FLT_N and U4_ILM. Add one on `VRP_3A` (J3 Rp) to verify the CC advertisement in the lab.
- **L8 — CH334F EEPROM pins:** pins 13 (`LED3/SCL`) and 21 (`LED4/SDA`) are left no-connect. Confirm with WCH that a floating EEPROM interface reliably selects the PSELF-based internal default at reset rather than attempting an EEPROM read.
- **L9 — Cost/manufacturing note:** 14 of 16 catalog parts are JLCPCB **Extended** (only the crystal C9002 and generic passives are Basic) → expect extended-part setup fees per unique extended part. Stock verified in the local JLCPCB cache: CH334F 4,701 · TPS259470LRPWR 2,828 · TYPE-C-31-M-17 9,030 · TLV76733DRVR 17,654 · TPS2121RUXR 37,162 · CP2102N 42,887 · TS3USB221A 92,249 · SN74LVC1G125 91,530 · TYPE-C-31-M-12 90,528 · TPD4E05U06 167,721 · BSS138P 463,553 · R31 (C852955) 5,699.
- **L10 — Fault LED loading:** LED3 (red) shares `HIL_FLT_N` with CP2102N GPIO.0. Logic verified correct (lit when FLT pulls low at ≈1.5 mA; node returns to ≈3.3 V through R34 when not faulted), but the TPS259470L datasheet does not state the FLT pin's sink capability — confirm 1.5 mA is acceptable.

---

## PCB layout review (added at v3)

The layout was out of scope for v2. It is now the dominant source of open work, so it is recorded here. All figures are from `kicad-cli 10.0.5 pcb drc` on the current board.

### 🔴 P1 — U4's imported footprint shorts `EN_HIL` and `HIL_FLT_N` (fabrication blocker)

`U4` uses the vendor-imported footprint `TPS259470ARPWR:RPW0010A-MFG` (from `symbols/ul_TPS259470ARPWR/`). Inspecting it shows **four filled polygons on `F.Cu` with no net assigned** — the exposed-pad copper drawn as polygons instead of as a pad. Because they overlap the signal pads, DRC reports:

| Violation | Detail |
|---|---|
| `shorting_items` ×2 | Polygon `<no net>` ↔ **Pad 1 `EN_HIL`**, and Polygon `<no net>` ↔ **Pad 4 `HIL_FLT_N`** |
| `clearance` ×2 | 0.000 mm against pads 7 (`Net-(U4-DVDT)`) and 10 (`ITIMER`) |
| `solder_mask_bridge` ×4 | the same polygons against the same pads |

**Impact if not fixed:** on a real board the eFuse's enable and fault pins would be shorted to floating copper — U4 would never enable and its fault flag would be unusable. This is the only true fab blocker in the design, and it is **independent of every schematic finding** in this report.

**Fix:** give the exposed pad a real pad carrying the `GND` net instead of no-net polygons, or delete the polygons and add a proper EP pad. It is an imported footprint, so re-exporting from the vendor tool is another option.

### P2 — Via geometry (resolved) and the remaining trap

| | |
|---|---|
| Placed vias | **64 × 0.5/0.3** (ring **0.100 mm**) + 1 × 0.6/0.3 — `annular_width` = **0** |
| Board rule | `min_via_annular_width = 0.1 mm` — **unchanged**, because 0.5/0.3 satisfies it exactly |
| **Still to do** | `Default` and `USB_90` netclasses still declare **`via = 0.45/0.3`**, and the pre-defined size list still offers `0.45/0.3`. **The next via routed on netclass defaults comes out at 0.45 → ring 0.075 mm → straight back into `annular_width`.** Set both netclasses (and the size list) to 0.5/0.3. |

**Why 0.5/0.3 rather than relaxing the rule:** JLCPCB's published figures contradict each other on annular ring — the capabilities page quotes a **0.13 mm** minimum, while their own Q&A notes that the *preferred minimum via* (0.35 mm pad on a 0.2 mm hole) implies **0.075 mm**. Two further points settled it: (a) KiCad measures the ring against the **drill** (0.075 mm) whereas the fab measures against the **finished hole**, which is smaller by ≈2× the plating thickness, so the real ring is ≈**0.10 mm**; (b) a 0.5 mm pad satisfies the existing 0.1 mm rule with **no rule change at all**, and JLCPCB's multilayer minimum drill is 0.15 mm, so 0.3 mm is comfortable. `0.45/0.25` would also pass unchanged, at the cost of barrel current (1.45 A vs 1.69 A per via at 10 °C).

**Ampacity note (relevant to any VSYS layer change):** the barrel is set by drill + plating, not by pad size. A 0.3 mm drill with 25 µm plating carries ≈**1.69 A at a 10 °C rise** (≈2.29 A at 20 °C). Against the 4.2 A VSYS ceiling that means **3 vias minimum, 4 for margin**; voltage drop is not limiting (4 vias → 1.3 mV). Place a **GND return via beside each power via**.

### P3 — USB_90 clearance rules (resolved)

`USB_90` netclass clearance was **0.5 mm**, which is geometrically impossible next to U2's 0.5 mm-pitch QFN-28 — it produced 45 pad-to-pad errors plus collateral hits on unrelated vias and pads that merely passed near a USB net. Now:

| Relationship | Clearance | Mechanism |
|---|---|---|
| Copper pour ↔ `USB_90` pair | **0.4 mm** | custom rule `Keep copper pour clear of USB_90 pairs` (scoped with `A.Type == 'Zone'`) |
| `USB_90` ↔ `USB_90` | 0.15 mm | custom rule `USB_90 intra-class clearance` |
| `USB_90` ↔ everything else | 0.15 mm | `USB_90` netclass |

Rules live in `llm-kicad.kicad_dru`. Note for KiCad's rule language: **there is no `A.Footprint` property** — `Type` takes `Bitmap/Dimension/Footprint/Graphic/Group/Leader/Pad/Target/Text/Text Box/Track/Via/Zone`, and footprint scoping is done with `insideCourtyard('U2')`; region scoping with `intersectsArea('name')` (`insideArea()` is deprecated).

**Zone fills must be recomputed after any rule change** (`B`, or Edit → Fill All Zones). Stored fills are static geometry, so a rule change without a re-fill appears as a wall of false clearance errors. That re-fill is what took `clearance` from 130 to 11.

### P4 — Current DRC state: 66 violations (down from 219)

| Violation | Count | Note |
|---|---|---|
| `unconnected_items` | 30 | routing in progress (from 229) |
| `starved_thermal` | 22 | zone thermal settings (`min_resolved_spokes` = 2); tune spoke width or connect solid |
| `drill_out_of_range` | 11 | **0.2 mm holes inside U1's and U8's footprints** vs the 0.3 mm board minimum — raise those via drills, or lower `min_through_hole_diameter` to 0.2 (JLC's multilayer minimum is 0.15 mm) |
| `clearance` | 11 | residual, including 0.175 mm track-to-track against the 0.2 mm `Default` rule |
| `silk_edge_clearance` / `silk_over_copper` / `silk_overlap` | 6 / 2 / 1 | silkscreen tail |
| `track_dangling` | 5 | orphaned stubs to delete |
| `solder_mask_bridge` | 4 | all from P1 |
| `shorting_items` | 2 | P1 |
| `lib_footprint_mismatch` | 2 | **U8** (`WSON-6-1EP_2x2mm…`) and **U6** (`SOT-23-5`) differ from the library copies — warnings; "Update symbols from library" clears them |
| `annular_width` | **0** | ✅ |

### P5 — Decoupling placement (ties to M2)

The four CP2102N decoupling caps sit **5.9–6.6 mm** from U2's power pins, against a ≤3 mm target for the 100 nF parts — see M2. Re-check after the current placement pass.

### PCB items to verify on hardware (not defects)

- 90 Ω differential routing, intra-pair skew (≤0.15 mm) and pair-to-pair spacing on the `USB_90` nets.
- Thermal relief on the U1/U4 exposed pads and the eFuse's thermal path.
- The M5 VSYS droop measurement (see M5).

---

## Verified correct — no action required

### USB-C power and CC (the explicit review question)

- **J3 Rp advertisement is spec-compliant.** USB-C spec **Table 4-24** defines three Rp methods per current level and includes an explicit **"resistor pull-up to 3.3 V ±5%"** column: **4.7 kΩ ±5% for 3.0 A @ 5 V** (10 kΩ is the value only for a 4.75–5.5 V pull-up). `R5/R6 = 4.7 kΩ` from a buffered 3.3 V source is therefore the correct implementation of the 3.3 V method, with vRd = 3.2 V × 5.1/(4.7+5.1) ≈ **1.67 V**, inside the sink's 3.0 A window **Table 4-36 (vRd-3.0 = 1.31–2.04 V)** with margin for Rd ±10%.
- **The 33 kΩ ∥ 5.6 kΩ variant is not needed** — the single 4.7 kΩ resistor is the same electrical value (4.79 kΩ) and is the spec's own entry for the 3.3 V column. This resolves the deviation noted in the previous report.
- **The buffer's limited output swing is not a problem.** The prompt's "5 V-to-GND swing" expectation applies to the *VBUS-referenced* Rp method (10 kΩ to 5 V). This design uses the 3.3 V-referenced method, whose correct pairing is 4.7 kΩ; the SN74LVC1G125's V_OH ≈ VCC − 0.1 V ≈ 3.2 V stays inside the required 3.3 V ±5% band.
- **J1/J2 Rd is correct:** 5.1 kΩ ±10% on **both** CC1 and CC2 of each sink (R1–R4), as §4.5.2.2.3.1 requires; a power-only sink receptacle (J2, no D+/D−) is explicitly permitted (§2.3.3).
- **"Detached" is signalled correctly:** when Rp is disabled, U6 goes high-impedance, meeting the spec's >zOPEN (126 kΩ) requirement. CC is **never** actively driven low (which would present a vRa-like condition).
- **VBUS discharge meets tVBUSOFF:** R36 (470 Ω) + Q3 discharge the 20 µF VBUS_HIL bulk from 5 V to 0.8 V in ≈17 ms (limit 650 ms), with software confirming within 150 ms.
- **20 µF source capacitance is legal:** Type-C Table 4-2's 10 µF ceiling applies to DRP ports; J3 is source-only (3000 µF allowed).
- **Behavioural deviation, documented and accepted:** J3 does not implement the mandatory source connection state machine (no CC monitoring for sink attach/detach — §4.4.2 / §4.5.2.2.7.1). It applies Rp then VBUS under software control and can leave VBUS live with nothing attached. This is explicitly listed as an accepted bench-only shortcut in `product_description.md` §7 and matches the architecture. Keep the fixture-only restriction and the labelling note; do not ship this as a general-purpose source.

### Power path

- **Priority mux configuration is correct:** IN1 = J1 (`VBUS_UP`), IN2 = J2 (`VBUS_PWR`), PR1 = GND, CP2 = IN2, OV1/OV2 = GND → the datasheet-documented selection (CP2 > PR1 selects IN2) with IN1 fallback when J2 is absent; tying OV1/OV2 to GND is the documented way to disable input OVP.
- **Reverse blocking needs no external parts:** the TPS2121 has always-on per-channel reverse-current blocking, so J1 cannot share the J2 load and cannot be back-fed from the output.
- **eFuse current limit is correct:** R31 = 931 Ω 0.1% → typ **3.581 A**, band **3.19–3.89 A** (< 4.0 A requirement met), and 931 Ω exceeds the 750 Ω UL 2367 minimum.
- **OVLO correct:** R29 49.9 kΩ (top) / R30 12 kΩ (bottom) → **6.19 V** rising (V_OV(R) 1.20 V), divider orientation correct, no floating pin.
- **dVdt correct:** C14 = 1 nF → ≈2 V/ms; with ≈58 µF of downstream bulk the J1 inrush is ≈45 mA — comfortably inside a Pi port budget (supports acceptance check #5).
- **ITIMER open** is documented valid (fastest response, active current limiting).
- **FLT** is open-drain active-low with R34 = 10 kΩ pull-up to +3.3V → GPIO.0.
- **Discharge logic verified:** EN_HIL high → Q2 on → Q3 gate low → discharge off; EN_HIL low → R37 (100 kΩ) turns Q3 on → discharge on. R36 dissipation ≈56 mW in 0805.
- **Sensing correct:** `VBUS_HIL_SNS` (10 k/18 k/1 k → 3.21 V at 5 V); `VBUS_PWR_SNS` and `VBUS_UP_SNS` (100 k/100 k/1 k → 2.5 V at 5 V) — all within GPIO input limits.
- **LDO correct:** TLV76733 with SNS tied to OUT (not floating), EN tied to IN (datasheet-sanctioned), C_IN = 1 µF (recommended), C_OUT = 10 µF + 100 nF (inside the 1–220 µF window), 1 A capability vs ≤150 mA design load.

### USB hub, control and data path

- **Hub reset requirement met exactly:** Q6/Q7 + R45/R46 hold U1 RESET# low whenever J1 VBUS is absent, release it when present, and never actively drive it high (matching the CH334F datasheet caution). Pull-down impedance via Q6 is a few ohms, well inside the datasheet's ≤800 Ω guidance.
- **CH334F configuration correct:** V5 and VDD33 both on +3.3V (the datasheet's recommended 3.3 V-only self-powered arrangement), PSELF tied high = self-powered, Y1 = 12 MHz **with no external load capacitors** (the datasheet recommends omitting them since XI/XO carry ≈16 pF internally), ~OVCUR# pulled up via R35 = 10 kΩ, unused ports 3/4 and the LED/EEPROM pins no-connected.
- **CP2102N correct where it matters:** RSTb has the datasheet's 1 kΩ pull-up (R47); VREGIN is tied to VDD (required when the internal regulator is unused); VBUS divider 22 k/47 k matches the datasheet's 22.1 k/47.5 k; integrated clock, no crystal; **all seven GPIOs used and mapped exactly as the architecture specifies** (GPIO.0 FLT, .1 PWR_SNS, .2 HIL_SNS, .3 UP_SNS, .4 VBUS_EN, .5 DATA_EN_N, .6 CC_EN_N); unused UART/charger/suspend pins no-connected.
- **Data path correct:** J1 D+/D- → U9 ESD → hub upstream; hub port 1 → CP2102N (fixed); hub port 2 → U5 → U10 ESD → J3 D+/D-, with SEL = GND so the switch passes 1D (J3) and OE driven by HIL_DATA_EN_N with an R48 = 100 kΩ pull-up (TI's floating-pin recommendation met). TS3USB221A is HS-capable (900 MHz BW, 6 pF on-capacitance).
- **ESD arrays meet the 0.5 pF budget:** TPD4E05U06 = 4 channels, **0.5 pF** I/O capacitance, **V_RWM 5.5 V** — and the design protects the **CC pins as well as the data pins** on all three connectors (J1 via U9, J3 via U10, J2 CC via U11), exceeding the requirement.
- **Connectors:** all four VBUS pins paralleled per connector, all GND/A12/B12 and SHIELD tied to GND, SBU1/SBU2 correctly no-connected on all three, J1/J3 A/B data pairs joined.
- **NC hygiene is clean:** **35** no-connect flags (6 on `01_power_cc`, 25 on `02_usb_hub_control`, 4 on `03_dut_interface`, 0 on `04_test_validation`), and ERC reports **no** unconnected-pin error other than `U1.5/U1.6` — i.e. every other unused pin already carries a flag. 0 dangling pins, 0 orphan labels, 0 dead-end wires.
- **Budget:** +3V3 load ≈55–102 mA (hub 42–85 mA + CP2102N 9.5–14 mA + logic/LEDs) against the 150 mA budget and a 1 A regulator.

### Verified at v3 (second pass)

- **U5's port-role swap is correct.** Re-derived from the netlist: `U1_P2_D+/-` → `U5.1/2` (1D), `J3_D+/-` → `U5.7/8` (2D), `SEL` (`U5.9`) tied to `GND`, `OE` driven by `HIL_DATA_EN_N` with an R48 = 100 kΩ pull-up. **No polarity inversion** — `D+` and `D-` are not crossed on either port — and the PCB pad-to-net mapping matches the schematic pad-for-pad.
- **U9/U10's "straight-through" wiring of the NC pads is TI's sanctioned technique, not a mistake.** On `TPD4E05U06`, pins 6/7/9/10 are marked *"Not connected; used for optional straight-through routing. Can be left floating or grounded."* The design ties each NC pad to the **same net as the functional pad opposite it at the same Y coordinate** (1↔10, 2↔9, 4↔7, 5↔6), which is exactly the intended arrangement — the pair runs through the footprint instead of branching off a stub. Verified in the PCB that all four mirror pairs sit at identical Y. **This adds no protection channels** (those pads have no die connection); the benefit is purely routing.
- **`CP2 → IN2` is required, not incidental.** Tying `CP2` to J2's VBUS puts the mux in **VREF mode** (TPS2121 Table 9-3: `CP2 ≥ VREF`, `PR1 < VREF` → `OUT = IN2`), giving **hard J2 priority** whenever J2 presents more than ≈1.06 V. That is what satisfies the product requirement *"the mux must not use voltage-based load sharing"* — grounding **both** `PR1` and `CP2` would select VCOMP (highest-voltage-wins) mode, which is exactly voltage-based sharing. It also enables the 5 µs fast-switchover path. Advisory only: the datasheet publishes no recommended input range for `CP2` (unlike its sibling control pins at 5.5 V), so if the out-of-contract higher-voltage input ever matters, a divider on `CP2` would add margin.

### Verified at v4 (third pass)

- **Pin-number vs pad-number audit over all 101 placed footprints: 0 mismatches.** Every symbol pin number was compared against the pad numbers of the footprint actually placed on the board — the machine check that would have caught **M1** and **H6**. It is the highest-value automated test on this design, because ERC's `footprint_filter` compares only name filters and never pin numbers. **Re-run it after any symbol or footprint change.**
- **Schematic ↔ PCB sync: 0 mismatches of 101.** Every component's `Footprint` field matches the footprint actually placed on the board (C5's 1210 land was re-synced into the PCB at 22:17 — see M11, whose remaining defect is a value/part mismatch only).
- **U4's `AUXOFF` (pin 3) and `ITIMER` (pin 10) are correctly left open.** TPS259470x pin table: `AUXOFF` is a **digital open-drain *output*** ("asserted High when the input supply is valid and channel has completed inrush sequence") used to sequence an auxiliary channel — there is no auxiliary eFuse on this board, so open is correct. `ITIMER` is documented as *"Leave this pin open for fastest [response]"*, which is the intended active-current-limiting behaviour. Both carry no-connect flags.
- **All three connectors' CC networks are correct.** J1 presents **Rd** 5.1 kΩ on `J1_CC1`/`J1_CC2` (R1/R2) and J2 presents **Rd** 5.1 kΩ on `J2_CC1`/`J2_CC2` (R3/R4) — both are sinks, as the architecture requires. J3 receives **Rp** from the buffered 3.3 V rail through R5 and R6 onto `J3_CC1`/`J3_CC2`, gated by `HIL_CC_EN_N` → U6 `!OE`: the 3 A advertisement is software-controlled and symmetric on both CC pins.
- **The hub port mapping matches the architecture's intent** even though the GPIO map does not: port 1 (`U1.11/12`) → CP2102N, so the control path is fixed and independent of the DUT; port 2 (`U1.9/10`) → U5, so only the DUT path is switched. Ports 3 and 4 are deliberately unused.
- **The `VBUS_HIL` discharge path behaves as the architecture's default state requires:** `EN_HIL` low → Q2 off → R37 (100 kΩ) pulls Q3's gate to +3V3 → Q3 (NMOS) on → `VBUS_HIL` discharged through R36 (470 Ω). So "enable low ⇒ discharge on" holds, matching the architecture's deterministic-default requirement.
- **Q2/Q3, Q6/Q7 and the hub reset network re-checked** against the netlist — no anomalies.
---

## Architecture & product compliance matrix

| # | Requirement (product / architecture) | Status | Evidence |
|---|---|---|---|
| 1 | Fixed control interface, independent of DUT state | ✅ Pass | CP2102N fixed on hub port 1; only port 2 switched |
| 2 | J3 disconnect/connect states (data/Rp/VBUS) | ✅ Pass | U5 OE# = HIL_DATA_EN_N, U6 OE# = HIL_CC_EN_N, U4 EN = EN_HIL; all default to the disconnected state |
| 3 | Control path survives J3 off/fault/unplugged | ✅ Pass | Separate hub port; FLT only reports |
| 4 | J2 preferred, J1 reverse-blocked, no J1 load sharing | ✅ Pass | TPS2121 PR1/CP2 + always-on reverse blocking |
| 5 | J1 fallback for low-current DUT tests | ✅ Pass | Automatic selection when J2 invalid |
| 6 | Downstream short/overload contained and reported | ✅ Pass | 3.89 A max limit, fast response, FLT → GPIO.0 |
| 7 | Hub held in reset when J1 VBUS absent | ✅ Pass | Q6/Q7 + R45/R46 as specified |
| 8 | 5.1 kΩ Rd on J1/J2 both CC pins | ✅ Pass | R1–R4, 5.1 kΩ |
| 9 | Fixed 3 A Rp on J3 from +3V3 | ✅ Pass | R5/R6 4.7 kΩ from buffered 3.3 V — spec-compliant (Table 4-24) |
| 10 | Voltage-based load sharing not used | ✅ Pass | No OR-ing diodes, no droop sharing |
| 11 | eFuse ≈3.59 A typ and <4.0 A max | ✅ Pass | R31 = 931 Ω → 3.58 A typ / 3.89 A max |
| 12 | OVP, fast OCP, thermal shutdown, FLT | ✅ Pass | OVLO 6.19 V; ITIMER open; FLT + pull-up |
| 13 | Controlled VBUS discharge | ✅ Pass | R36/Q3, ≈17 ms to 0.8 V |
| 14 | TVS at all three USB-C VBUS pins | ✅ Pass (H3 fixed) | D1/D2 now SMAJ6.5A (C123817), matching D3 |
| 15 | Low-capacitance ESD at USB/CC pins | ✅ Pass | U9/U10/U11, 0.5 pF, V_RWM 5.5 V |
| 16 | GPIO mapping per architecture §6 | ❌ **Fail (H7)** | The schematic is self-consistent, but it contradicts architecture §6 on **6 of 7** GPIO pins — and the documented configuration would leave U4 disabled and J3 unable to advertise (H7) |
| 17 | Deterministic default = disconnected state | ✅ Pass (H4 fixed) | R28 = 10 kΩ → EN_HIL ≈0.3 V worst case |
| 18 | Mux current limit within device limits | ✅ Pass (H2 fixed) | R32 = 24 kΩ → ≈4.23 A, inside the 18–100 kΩ range |
| 19 | All ICs have datasheet-complete external parts | ✅ Pass (M2 fixed) | 4.7 µF + 100 nF at both U2 power pins (`C1779` Basic at 0805 + 0402 100 nF); **placement at U2 still to verify** |
| 20 | Qualified 5 V input boundary, no PD/BC1.2 | ✅ Pass | No PD/BC1.2/VCONN circuitry; no SBU |
| 21 | Component packages match assigned footprints | ✅ Pass (H1, M1, H6 fixed) | CH334F now 4×4 mm / 0.5 mm; J2 uses a six-pin symbol matched to its six-pad footprint; Y1 now `Crystal_GND24` on the four-pad land |
| 22 | BOM part numbers match component values | ⚠️ **Partial (H5 fixed; M11 open)** | R33/R34/R35 → C25744 (10 kΩ); whole-board value/part/voltage cross-check is clean except **C5**, whose value `100uF 10V` does not match its coded 47 µF part (M11) |

**Unverified / manual-review items (not defects):**

- PCB-side items (90 Ω differential routing, skew, via count, creepage, thermal relief, EP via arrays, decoupling placement) are out of scope for a schematic review — carry into the layout review.
- J1→J2 switchover droop with 47 µF (M5) and the 0–3 A load-step sag on VBUS_HIL require bench measurement.
- CH334F PSELF/reset behaviour and EEPROM-pin floating behaviour should be confirmed on the prototype (L8).
- R31/U4_ILM trip behaviour, thermal latch recovery and reverse-current blocking need prototype testing (acceptance checks #2, #4, #7).
- ~~The J2 symbol/footprint mapping (M1) must be re-verified after the symbol is replaced.~~ **Closed at v4** — J2's seven symbol pins match its seven footprint pads, as part of the 101-footprint audit.

---

## Verification basis

- **Extraction:** `inventory_project.py`, `extract_kicad_sch.py` (all four sub-sheets + root), `build_review_context.py` → JSON in `/tmp/rev/` (211 components, 10 IC candidates, 53 no-connects, all nets traced pin-by-pin). *(v2/v3 basis — the design has changed since; the current count is 35 no-connect flags.)*
- **Formal check:** `kicad-cli 10.0.5 sch erc` — full report examined including excluded items and the disabled-check list. *(At v4 all exclusions and severity overrides have been removed, so the report is now exactly what the design produces.)*
- **v4 method:** `kicad-cli sch erc` + `kicad-cli sch export netlist --format kicadsexpr`, then a script that parses the netlist `pinfunction`/`ref`/`pin` triples and every PCB footprint's pad list, and compares each component's schematic symbol pin numbers, `Footprint` field and placed footprint (101 components) — the audit that produced the 0-mismatch result and H7.
- **Datasheets fetched and extracted at review time:** TPS2121 (SLVSEA3F), TPS25947x (SLVSFC9C), CP2102N (Rev 1.5), CH334/335 (V2.91), TS3USB221A (SCDS277C), TLV767 (SLVSE84D), SN74LVC1G125 (SCES223U), TPD4E05U06 → cached in `/tmp/rev/ds/`.
- **Specification:** USB Type-C Cable and Connector Specification (USB-IF), Tables 4-2, 4-16, 4-24, 4-25, 4-29, 4-36 and §§4.4.2, 4.5.2.x, 4.8.1.1.
- **Cross-checks:** footprint pad lists read from the installed KiCad footprint libraries; LCSC/JLCPCB catalog (local cache, 797,477 parts) for package and stock confirmation.
- **Independent verification pass:** each H/M finding was re-derived from primary evidence (raw schematic values + datasheet text) rather than from a single tool summary; two previously recorded claims were corrected (R32's current limit; D1/D2's suitability as 5 V TVS).

- **Post-fix verification pass, first round (2026-09-27):** after the H1–H5 fixes, all four sheets were re-parsed (values, footprints and LCSC codes read back as intended), the whole-board value/part cross-check was re-run (0 mismatches), and ERC was re-run through `kicad-cli` (unchanged: 1 excluded error, 3 warnings).
- **Second pass (v3), and how each correction was caught:** H6 came from comparing the PCB pad blocks against three vendor pin tables; M1 and M2 were re-verified from a fresh netlist export (pin-by-pin: `Net-(U1-XI)`, `Net-(U1-XO)`, `GND`, and J2's seven pads) plus the PCB footprint instances; the ERC facts in M3 were read from `.kicad_pro` (`rule_severities`, `erc_exclusions`) rather than from a tool summary, which is how v2's wrong net name was caught; M6 was corrected from the TPS2121 pin table and truth table; the PCB figures come from `kicad-cli pcb drc` plus direct reads of the board and project files; M11 came from a catalogue lookup triggered by the M5 evaluation.
- **Third pass (v4), 2026-09-27:** the design was re-checked from scratch against the saved files after the owner's edit pass (all four sheets and the project re-saved at 22:01). This pass added **H7** and the automated **pin-number/pad-number audit** (101 footprints, 0 mismatches), re-derived the hub port usage and the three-way C5 mismatch, and confirmed `AUXOFF`/`ITIMER` against the TPS25947 pin table. H7 came only from reading the netlist's `pinfunction` values alongside the architecture's own table — no automated check compares a document to a netlist, so repeat it by hand whenever the GPIO manifest is regenerated. **Current state: ERC 4 errors / 2 warnings, nothing suppressed · DRC 66 violations · one fabrication blocker (P1).**

## Where to continue

Priority order for the next session. Items 1–3 are schematic; 4 and below are layout.

1. **H7 — reconcile the GPIO map.** Edit `architecture/architecture_final.md` §6, the `cp2102n_config.hex` release-gate instruction, and the `schematic_overview.md` diagram to the pin map in H7, then generate the CP2102N configuration image from the **netlist**. No schematic or board change. *This is first because the fixture cannot work until it is done — as written today, U4 would never enable and J3 would never advertise.*
2. **M3 — four flags to a clean ERC.** Add a `PWR_FLAG` on GND and on `VBUS_PWR` (both on `04_test_validation`), and no-connect flags on `U1` pins 5 and 6. Then decide `U1`'s and `J1`'s `lib_symbol_mismatch` deliberately. Target: **0 errors, 0 suppressed checks.**
3. **M11 — C5.** One field either way: code `C23742` (`CL32A107MPVNNNE`, 1210, 100 µF, 10 V, Extended) to match the value text and land that are already in place, **or** revert to 47 µF (`47uF 10V`, `C96123` Basic) and put the land back to 1206. M5 says 100 µF is not needed, so reverting is the cheaper engineering call.
4. **P1 — U4's footprint polygons** (the only *fabrication* blocker): give the exposed pad a real `GND` pad, or delete the four no-net `F.Cu` polygons.
5. **Restore the netclass via size** in `Default` and `USB_90` to `0.5/0.3`, and drop `0.45/0.3` from the pre-defined size list — otherwise the next via routed on netclass defaults comes out at 0.45 and re-breaks `annular_width`.
6. **Finish the layout:** route the last ~30 nets, clear `starved_thermal`, raise the 0.2 mm drills inside U1/U8, tidy silkscreen and dangling tracks.
7. **Decisions still owed:** **M4** (VSYS UVLO divider on U4 EN/UVLO), **M8** (50 Ω on U5's unused `2D±`), **M9** (latch-off recovery in the software contract), **M10** (clear the 43 stale `instances` records so tooling can edit U1/U2/U5/U6/U9/U10/J1/J3 again), and **L1–L10** (naming and documentation tidies). **M5** is settled by the C5 choice in item 3.

---

*Supersedes `schematic_review_report_v1_archived.md` (also recoverable from git history).*
