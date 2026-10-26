# PQ35 GRA button trace analysis

## Artifact

- File: `C:\Users\МИХАИЛ\Downloads\Telegram Desktop\нажатия_ктопок_увеличения_скорости`
- Format: SQLite 3, CANHacker frames table
- SHA-256: `eb92f33b766b078814b31b01311799917e68886059e336cf5ab8eb446544bd3d`
- Frames: 1,708,356
- Trace span: approximately 728.0 s
- Channels: channel 1 only

## Observed GRA_Neu timing

The trace contains 36,422 frames with CAN ID `0x38A` (`GRA_Neu`). The normal
period is 20 ms (median 20,000 us). The observed minimum and maximum deltas
are 15.819 ms and 39.611 ms; occasional longer gaps are trace/scheduling
gaps, not a reason to transmit without a fresh stock counter.

The payload's final byte is always zero in this capture. The first byte carries
the checksum/counter representation used by the existing DBC packer; it must
not be copied as a constant.

## Button bits

The mapping is from `opendbc_repo/opendbc/dbc/vw_pq.dbc`, `GRA_Neu`:

| Signal | DBC bit | Observed byte-1 pattern |
|---|---:|---|
| `GRA_Hauptschalt` | 8 | normal `0x81` when main switch is on |
| `GRA_Abbrechen` | 9 | preserve from stock |
| `GRA_Down_kurz` | 10 | `0x85` during down pulses |
| `GRA_Up_kurz` | 11 | `0x89` during up pulses |
| `GRA_Neu_Setzen` | 16 | preserve/set through the stock frame path |
| `GRA_Recall` | 17 | preserve from stock |
| `COUNTER` | 20..23 | rolling counter; synchronize before injection |

Observed non-normal `byte1` totals are 998 frames of `0x85`, 1,134 frames of
`0x89`, and 342 frames of `0x80`. The `0x80` runs occur around 274.188–275.088
s and 782.142–788.042 s and represent the main switch being off in the DBC
interpretation.

## Implications for ICBM

The current Volkswagen ICBM implementation uses:

- fresh `GRA_Neu` counter as the send gate;
- one short press no more often than `BUTTON_STEP_INTERVAL = 0.1 s`;
- stock GRA fields copied by `create_acc_buttons_control()`;
- DBC packer for counter/checksum generation.

That 100 ms ICBM interval is intentionally slower than the 20 ms stock
period. It prevents holding a short-press bit continuously while still
allowing repeated speed changes. The 20 ms source period must therefore not be
used as the injection interval.

## Scope and limitations

- No `0x5C1` frames occur in this artifact, so it is an ACC/GRA trace, not a
  steering-wheel `mMFL_Tasten` trace.
- Exact semantic labels for any source-specific `dirType` or unmarked user
  action require markers or an independent trace annotation.
- This analysis validates frame timing and bit structure only. It does not
  prove that the vehicle accepts an injected frame; that remains a stationary
  bench requirement.
