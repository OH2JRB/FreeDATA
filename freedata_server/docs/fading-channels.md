# Fading channels and aurora

How the FreeDATA modes cope with fading HF channels, and in particular with
auroral (high-latitude) paths. The numbers come from a simulator, not from the
air; see the limits at the end.

## 1. Tools

| File | Purpose |
|---|---|
| `tools/channel_sim.py` | The channel model and `run_mode()`, which sends frames of one mode through a channel and counts the frames decoded with the right payload |
| `tools/fading_measure.py` | Runs every mode through every channel preset at several SNRs and prints Markdown tables like the ones below |
| `tests/test_fading_channel.py` | Checks the simulator itself (Doppler spread, SNR, power, Doppler shift) and that two modes decode on a clean channel |

```
python tools/fading_measure.py                       # all modes, channels and SNRs, about 18 minutes
python tools/fading_measure.py --channels hl_moderate --snr 10 20 --frames 10
python tools/fading_measure.py --custom 5 2          # Doppler spread 5 Hz, path delay 2 ms
python tools/fading_measure.py --shift 20            # add a 20 Hz Doppler shift
```

## 2. Channel model

A Watterson model, the usual model for HF channel simulators (codec2's `ch`, the
ITU-R F.1487 test channels):

- two paths of equal mean power, the second delayed by the path delay;
- each path with independent Rayleigh fading whose Doppler spectrum is Gaussian;
  the Doppler spread is the two-sided width, 2 sigma of that spectrum;
- an optional fixed Doppler shift;
- white noise, set by the SNR in a 3 kHz bandwidth against the mean power while
  the transmitter is on, as codec2 measures it.

The modes are modulated by `freedata_server.modulator.Modulator.create_burst()`
(preamble, one frame, postamble, 500 ms gap, as in an ARQ burst) and decoded with
`freedv_rawdatarx` on 8 kHz audio, like `demodulator.py` does, but offline.

| Preset | Doppler spread | Delay | Stands for |
|---|---|---|---|
| `awgn` | 0 | 0 | noise only |
| `mid_moderate` | 0.5 Hz | 1 ms | mid-latitude moderate |
| `mid_disturbed` | 1 Hz | 2 ms | mid-latitude disturbed (codec2 MPP) |
| `hl_moderate` | 10 Hz | 3 ms | high-latitude moderate, auroral |
| `hl_disturbed` | 30 Hz | 7 ms | high-latitude disturbed, strong aurora |

The high-latitude presets follow the ITU-R F.1487 high-latitude channels. Real
auroral paths vary a lot; 10 to 30 Hz of spread is typical of disturbed
conditions, not a worst case.

## 3. Results

Share of frames decoded, 20 frames per cell, seed 1. Payload bps is the payload
bit rate of one burst on the air, preamble and postamble included. The modes of
the ARQ speed levels (`arq_session.SPEED_LEVEL_DICT`) are in bold.
`signalling` is DATAC13 and `signalling_ack` is DATAC14.

### `awgn`: no fading, noise only (Doppler spread 0 Hz, delay 0 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| signalling | 51 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| signalling_ack | 35 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| datac0 | 170 | 0% | 100% | 100% | 100% | 100% | 100% | 100% |
| datac3 | 296 | 0% | 100% | 100% | 100% | 100% | 100% | 100% |
| **datac1** | 927 | 0% | 0% | 100% | 100% | 100% | 100% | 100% |
| data_ofdm_200 | 51 | 0% | 0% | 0% | 0% | 100% | 100% | 100% |
| data_ofdm_250 | 80 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| **data_ofdm_500** | 255 | 0% | 100% | 100% | 100% | 100% | 100% | 100% |
| data_ofdm_1700 | 927 | 0% | 0% | 100% | 100% | 100% | 100% | 100% |
| **data_ofdm_2438** | 1797 | 0% | 0% | 100% | 100% | 100% | 100% | 100% |
| data_vhf_1 | 176 | 50% | 60% | 60% | 60% | 60% | 60% | 60% |

### `mid_moderate`: mid-latitude moderate (Doppler spread 0.5 Hz, delay 1 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 60% | 100% | 95% | 100% | 100% | 100% | 100% |
| signalling | 51 | 70% | 95% | 100% | 100% | 95% | 100% | 100% |
| signalling_ack | 35 | 60% | 95% | 95% | 100% | 100% | 100% | 100% |
| datac0 | 170 | 10% | 50% | 95% | 95% | 100% | 100% | 100% |
| datac3 | 296 | 0% | 40% | 100% | 100% | 100% | 100% | 100% |
| **datac1** | 927 | 0% | 0% | 75% | 100% | 100% | 100% | 100% |
| data_ofdm_200 | 51 | 0% | 0% | 0% | 10% | 80% | 100% | 100% |
| data_ofdm_250 | 80 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| **data_ofdm_500** | 255 | 10% | 75% | 100% | 100% | 100% | 100% | 100% |
| data_ofdm_1700 | 927 | 0% | 0% | 80% | 100% | 100% | 100% | 100% |
| **data_ofdm_2438** | 1797 | 0% | 0% | 25% | 95% | 95% | 100% | 100% |
| data_vhf_1 | 176 | 10% | 40% | 45% | 40% | 55% | 55% | 50% |

### `mid_disturbed`: mid-latitude disturbed (codec2 MPP) (Doppler spread 1 Hz, delay 2 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 80% | 95% | 100% | 100% | 100% | 100% | 100% |
| signalling | 51 | 75% | 90% | 100% | 100% | 100% | 100% | 100% |
| signalling_ack | 35 | 60% | 90% | 100% | 95% | 100% | 100% | 100% |
| datac0 | 170 | 5% | 60% | 100% | 100% | 100% | 100% | 100% |
| datac3 | 296 | 0% | 70% | 90% | 100% | 100% | 100% | 100% |
| **datac1** | 927 | 0% | 0% | 60% | 100% | 100% | 100% | 100% |
| data_ofdm_200 | 51 | 0% | 0% | 0% | 15% | 55% | 95% | 85% |
| data_ofdm_250 | 80 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| **data_ofdm_500** | 255 | 0% | 90% | 100% | 100% | 100% | 100% | 100% |
| data_ofdm_1700 | 927 | 0% | 0% | 75% | 100% | 100% | 100% | 100% |
| **data_ofdm_2438** | 1797 | 0% | 0% | 25% | 100% | 100% | 100% | 100% |
| data_vhf_1 | 176 | 0% | 40% | 55% | 55% | 60% | 60% | 60% |

### `hl_moderate`: high-latitude moderate, auroral (Doppler spread 10 Hz, delay 3 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| signalling | 51 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| signalling_ack | 35 | 0% | 0% | 0% | 0% | 0% | 10% | 0% |
| datac0 | 170 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| datac3 | 296 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| **datac1** | 927 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_ofdm_200 | 51 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_ofdm_250 | 80 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| **data_ofdm_500** | 255 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_ofdm_1700 | 927 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| **data_ofdm_2438** | 1797 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_vhf_1 | 176 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |

### `hl_disturbed`: high-latitude disturbed, strong aurora (Doppler spread 30 Hz, delay 7 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| signalling | 51 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| signalling_ack | 35 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| datac0 | 170 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| datac3 | 296 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| **datac1** | 927 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_ofdm_200 | 51 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_ofdm_250 | 80 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| **data_ofdm_500** | 255 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_ofdm_1700 | 927 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| **data_ofdm_2438** | 1797 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_vhf_1 | 176 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |

### Where the modes break

10 frames per cell at 20 dB SNR, custom channels (`--custom`):

| Doppler spread | Delay | datac4 | DATAC13 | DATAC14 | datac0 | data_ofdm_500 | datac1 | data_ofdm_2438 | data_vhf_1 |
|---|---|---|---|---|---|---|---|---|---|
| 2 Hz | 2 ms | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 60% |
| 3 Hz | 2 ms | 100% | 100% | 100% | 90% | 100% | 100% | 100% | 60% |
| 5 Hz | 2 ms | 70% | 60% | 10% | 20% | 50% | 40% | 30% | 60% |
| 7 Hz | 2 ms | 0% | 20% | 10% | 0% | 0% | 0% | 0% | 60% |
| 10 Hz | 0 ms | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 60% |
| 10 Hz | 3 ms | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| 1 Hz | 7 ms | 80% | 100% | 90% | 60% | 50% | 40% | 10% | 10% |

## 4. Findings

- **The OFDM modes stop working between 3 and 7 Hz of Doppler spread**, whatever the
  SNR. That covers every HF mode FreeDATA uses, including the ARQ signalling
  modes (DATAC13, DATAC14) and the most robust speed level (DATAC4). On the
  auroral presets (10 and 30 Hz) nothing decodes, so no ARQ session can even
  start. More SNR does not help: the receiver cannot follow the channel.
- **Up to about 2 Hz the modes behave as designed**; the mid-latitude presets
  cost a few dB, more for the fast modes.
- **Long path delays also hurt**: with 7 ms between the paths (longer than the
  6 ms cyclic prefix of the OFDM modes) even 1 Hz of spread costs the fast modes
  most of their frames.
- **The FSK mode `data_vhf_1` is not affected by Doppler spread**: at 10 Hz of spread
  without a path delay it decodes as well as on a clean channel. It fails once
  the second path is 3 ms late, because its 5 ms symbols (200 baud) are then
  smeared into each other. An FSK mode with longer symbols, sized for HF, is the
  most promising way to get through auroral paths.
- Because the speed level is chosen from the SNR alone, FreeDATA would pick
  DATAC1 or faster on a strong auroral signal, which then fails completely.
- `data_ofdm_250` decodes nothing, even on a clean channel at 30 dB. It is not
  used by the ARQ speed levels; its configuration in `codec2.py` probably needs
  fixing.

## 5. Limits

- Simulated channels only. Real auroral paths also have fast changes of the
  Doppler shift, more than two paths and absorption, none of which is modelled
  here.
- One burst per frame with 500 ms gaps; the ARQ timing and retries are not part
  of the test.
- `data_vhf_1` decodes only about 60% of the frames even on a clean channel in
  this harness. It is sent without preamble and postamble, as with
  `EXP.enable_vhf`; the losses are probably at burst starts and were not
  investigated.
- The results depend on the seed; 20 frames per cell give a rough share, not an
  exact frame error rate.
