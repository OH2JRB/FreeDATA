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
python tools/fading_measure.py --fsk --stream        # also the HF FSK candidates, see section 6
python tools/fading_measure.py --fsk --restart       # FSK bursts with the receiver restart, see section 7
python tools/fading_measure.py --restart --stream --modes datac4 data_fsk4_50   # the HF FSK mode, see section 8
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
- `data_vhf_1` and the FSK candidates decode only 50 to 60% of single-frame
  bursts even on a clean channel; see section 7 for the cause and a fix.
- The results depend on the seed; 20 frames per cell give a rough share, not an
  exact frame error rate.

## 6. HF FSK candidates

The FSK finding above suggests an FSK mode with longer symbols for auroral
paths. `tools/channel_sim.py` has a list of candidates, `FSK_CANDIDATES`, opened
directly from codec2's FSK_LDPC mode (`freedv_open_advanced`); FreeDATA itself
has none of them. All fit in about 500 Hz, like `data_ofdm_500`.

| Candidate | Tones | Symbol rate | Symbol length | Tone spacing | LDPC code | Bandwidth | Payload | Payload bps |
|---|---|---|---|---|---|---|---|---|
| `fsk4_100` | 4 | 100 baud | 10 ms | 100 Hz | H_256_512_4 | 500 Hz | 30 bytes | 88 |
| `fsk4_50` | 4 | 50 baud | 20 ms | 100 Hz | H_256_512_4 | 400 Hz | 30 bytes | 44 |
| `fsk4_50w` | 4 | 50 baud | 20 ms | 150 Hz | H_256_512_4 | 550 Hz | 30 bytes | 44 |
| `fsk2_100` | 2 | 100 baud | 10 ms | 200 Hz | H_256_512_4 | 400 Hz | 30 bytes | 44 |
| `fsk4_25` | 4 | 25 baud | 40 ms | 100 Hz | H_256_512_4 | 350 Hz | 30 bytes | 22 |
| `fsk4_100_long` | 4 | 100 baud | 10 ms | 100 Hz | H_1024_2048_4f | 500 Hz | 126 bytes | 97 |

For comparison, `datac4` (the most robust ARQ speed level) carries 80 bps and
`data_vhf_1` (4FSK, 200 baud, 400 Hz spacing) 176 bps.

### Measurements

`python tools/fading_measure.py --modes datac4 signalling data_vhf_1 fsk4_100
fsk4_50 fsk4_50w fsk2_100 fsk4_25 fsk4_100_long --stream --frames 10
--channels awgn mid_disturbed hl_moderate hl_disturbed`, 10 frames per cell.
Rows marked "stream" send the frames back to back; the others send one frame
per burst with 500 ms gaps, as ARQ does.

#### `awgn`: no fading, noise only (Doppler spread 0 Hz, delay 0 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| signalling | 51 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| data_vhf_1 | 176 | 30% | 60% | 60% | 60% | 60% | 60% | 60% |
| data_vhf_1, stream | 176 | 90% | 100% | 100% | 100% | 100% | 100% | 100% |
| *fsk4_100* (500 Hz) | 88 | 50% | 60% | 60% | 60% | 60% | 60% | 60% |
| *fsk4_100* (500 Hz), stream | 88 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| *fsk4_50* (400 Hz) | 44 | 50% | 50% | 50% | 50% | 50% | 50% | 50% |
| *fsk4_50* (400 Hz), stream | 44 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| *fsk4_50w* (550 Hz) | 44 | 50% | 50% | 50% | 50% | 50% | 50% | 50% |
| *fsk4_50w* (550 Hz), stream | 44 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| *fsk2_100* (400 Hz) | 44 | 50% | 50% | 50% | 50% | 50% | 50% | 50% |
| *fsk2_100* (400 Hz), stream | 44 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| *fsk4_25* (350 Hz) | 22 | 50% | 50% | 40% | 50% | 50% | 50% | 50% |
| *fsk4_25* (350 Hz), stream | 22 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| *fsk4_100_long* (500 Hz) | 97 | 50% | 50% | 50% | 50% | 50% | 50% | 50% |
| *fsk4_100_long* (500 Hz), stream | 97 | 90% | 100% | 100% | 100% | 100% | 100% | 100% |

#### `mid_disturbed`: mid-latitude disturbed (codec2 MPP) (Doppler spread 1 Hz, delay 2 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 80% | 100% | 100% | 100% | 100% | 100% | 100% |
| signalling | 51 | 70% | 100% | 100% | 100% | 100% | 100% | 100% |
| data_vhf_1 | 176 | 0% | 40% | 60% | 60% | 60% | 60% | 60% |
| data_vhf_1, stream | 176 | 20% | 50% | 90% | 100% | 90% | 100% | 100% |
| *fsk4_100* (500 Hz) | 88 | 40% | 60% | 60% | 60% | 60% | 60% | 60% |
| *fsk4_100* (500 Hz), stream | 88 | 50% | 80% | 90% | 100% | 100% | 100% | 100% |
| *fsk4_50* (400 Hz) | 44 | 50% | 50% | 50% | 50% | 50% | 50% | 50% |
| *fsk4_50* (400 Hz), stream | 44 | 100% | 90% | 90% | 100% | 100% | 90% | 100% |
| *fsk4_50w* (550 Hz) | 44 | 50% | 50% | 50% | 50% | 50% | 50% | 50% |
| *fsk4_50w* (550 Hz), stream | 44 | 90% | 100% | 100% | 80% | 100% | 90% | 100% |
| *fsk2_100* (400 Hz) | 44 | 40% | 50% | 50% | 50% | 50% | 50% | 50% |
| *fsk2_100* (400 Hz), stream | 44 | 80% | 90% | 100% | 100% | 100% | 100% | 90% |
| *fsk4_25* (350 Hz) | 22 | 50% | 50% | 50% | 50% | 40% | 50% | 40% |
| *fsk4_25* (350 Hz), stream | 22 | 100% | 100% | 100% | 90% | 90% | 90% | 100% |
| *fsk4_100_long* (500 Hz) | 97 | 20% | 50% | 50% | 50% | 50% | 50% | 50% |
| *fsk4_100_long* (500 Hz), stream | 97 | 30% | 90% | 100% | 100% | 100% | 100% | 100% |

#### `hl_moderate`: high-latitude moderate, auroral (Doppler spread 10 Hz, delay 3 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| signalling | 51 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_vhf_1 | 176 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_vhf_1, stream | 176 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| *fsk4_100* (500 Hz) | 88 | 0% | 50% | 50% | 60% | 60% | 60% | 50% |
| *fsk4_100* (500 Hz), stream | 88 | 40% | 100% | 100% | 90% | 100% | 100% | 100% |
| *fsk4_50* (400 Hz) | 44 | 50% | 50% | 50% | 50% | 50% | 50% | 50% |
| *fsk4_50* (400 Hz), stream | 44 | 90% | 100% | 100% | 90% | 90% | 90% | 90% |
| *fsk4_50w* (550 Hz) | 44 | 40% | 50% | 50% | 50% | 50% | 50% | 50% |
| *fsk4_50w* (550 Hz), stream | 44 | 90% | 100% | 100% | 90% | 90% | 100% | 100% |
| *fsk2_100* (400 Hz) | 44 | 40% | 50% | 50% | 50% | 50% | 50% | 50% |
| *fsk2_100* (400 Hz), stream | 44 | 90% | 100% | 100% | 100% | 100% | 100% | 90% |
| *fsk4_25* (350 Hz) | 22 | 40% | 50% | 50% | 50% | 40% | 50% | 50% |
| *fsk4_25* (350 Hz), stream | 22 | 90% | 100% | 90% | 100% | 90% | 90% | 100% |
| *fsk4_100_long* (500 Hz) | 97 | 0% | 50% | 50% | 50% | 50% | 50% | 50% |
| *fsk4_100_long* (500 Hz), stream | 97 | 20% | 100% | 100% | 100% | 100% | 100% | 100% |

#### `hl_disturbed`: high-latitude disturbed, strong aurora (Doppler spread 30 Hz, delay 7 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| signalling | 51 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_vhf_1 | 176 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_vhf_1, stream | 176 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| *fsk4_100* (500 Hz) | 88 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| *fsk4_100* (500 Hz), stream | 88 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| *fsk4_50* (400 Hz) | 44 | 20% | 30% | 40% | 50% | 30% | 40% | 40% |
| *fsk4_50* (400 Hz), stream | 44 | 20% | 90% | 60% | 70% | 60% | 50% | 90% |
| *fsk4_50w* (550 Hz) | 44 | 20% | 40% | 40% | 40% | 40% | 30% | 40% |
| *fsk4_50w* (550 Hz), stream | 44 | 40% | 70% | 60% | 80% | 60% | 100% | 80% |
| *fsk2_100* (400 Hz) | 44 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| *fsk2_100* (400 Hz), stream | 44 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| *fsk4_25* (350 Hz) | 22 | 10% | 0% | 0% | 0% | 0% | 10% | 0% |
| *fsk4_25* (350 Hz), stream | 22 | 10% | 0% | 10% | 0% | 10% | 10% | 0% |
| *fsk4_100_long* (500 Hz) | 97 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| *fsk4_100_long* (500 Hz), stream | 97 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |

### What the FSK measurements show

- **On `hl_moderate` (10 Hz, 3 ms) every candidate works** where all current modes fail:
  90 to 100% of the frames from 0 dB up when streamed. `data_vhf_1` still fails,
  so the longer symbols (10 ms or more instead of 5 ms) are what makes the difference.
- **On `hl_disturbed` (30 Hz, 7 ms) only the 50 baud 4FSK candidates get through**:
  `fsk4_50w` 60 to 100% and `fsk4_50` 50 to 90% when streamed. The 100 baud candidates
  fail because the 7 ms echo covers most of a 10 ms symbol, and 25 baud fails because
  a 40 ms symbol is longer than the channel stays the same at 30 Hz of spread.
  50 baud (20 ms symbols) is the sweet spot, and the wider 150 Hz tone spacing helps
  a little.
- The price is speed: 44 bps of payload for the 50 baud candidates, against 80 bps
  for `datac4` on a good channel. On an auroral path the comparison is 44 bps
  against nothing.
- **FSK in single-frame bursts loses about half of the frames, even without noise.**
  codec2's FSK receiver decodes a frame only about one frame length after it ends,
  and with silence between bursts it misses frames depending on how the gap lines up
  with its frame-length processing. Back-to-back frames all decode. This affects
  `data_vhf_1` too (FreeDATA's VHF mode). Before an FSK mode can carry ARQ bursts
  this has to be fixed, for example with a preamble the receiver can sync on, or by
  sending FSK frames as one continuous transmission.

### Next steps this points to

1. Fix FSK burst acquisition: done in the simulator, see section 7.
2. Add a 50 baud 4FSK mode (`fsk4_50w` or close to it) to `codec2.py` and the
   modulator, decoders and frame handling (done: `data_fsk4_50`, section 8), and
   use it for signalling and as the lowest ARQ speed level on disturbed paths
   (not yet, see section 8).
3. Choose that level from failures and channel measurements, not from SNR alone,
   since the auroral channel looks strong but defeats the OFDM modes.

## 7. FSK burst acquisition

### Why single-frame FSK bursts are lost

From codec2's FSK_LDPC receiver (`freedv_rx_fsk_ldpc_data()` in `src/freedv_fsk.c`, read in the
codec2 main branch; FreeDATA ships its own libcodec2 build, whose behaviour matches):

- The receiver collects demodulated bits one frame length at a time into a
  buffer of two frames and processes the buffer once per frame length. A frame is
  decoded only after the receiver has collected the frame length that follows
  it, which is why the last frame of a transmission needs more audio after it.
- Out of sync (state 0) it searches every bit position of the buffer for the
  unique word and accepts the best one with at most 5 bit errors.
- In sync (state 1) it checks the unique word **only at the position of the
  frame it last decoded**. After one bad check (more than 6 errors,
  `fsk_ldpc_baduw_thresh = 1`) it falls back to searching, but only from the
  next frame length on.
- So after a decoded burst, the receiver spends one frame length checking the
  wrong position. A burst that starts during that time has shifted out of the
  search range by the time the receiver searches again, and is lost. Back-to-back
  frames keep the same position and all decode; bursts with gaps of less than
  about two frame lengths lose about every other frame.
- FreeDATA cannot reset this state: `freedv_set_sync()` (called by
  `Demodulator.reset_data_sync()` while transmitting) and
  `freedv_set_frames_per_burst()` act only on the OFDM modes. While FreeDATA
  transmits, the demodulator drops the received audio, so the FSK receiver
  does not even see time pass, and the next burst arrives at another position.

### What was tried

20 single-frame bursts with 500 ms gaps:

| Mode | Channel | SNR | As is | Receiver restart | FSK preamble | Both | Back to back |
|---|---|---|---|---|---|---|---|
| data_vhf_1 | awgn | 20 dB | 65% | 100% | 75% | 100% | 100% |
| data_vhf_1 | awgn | 0 dB | 65% | 95% | 75% | 100% | 100% |
| fsk4_100 | awgn | 20 dB | 55% | 100% | 60% | 95% | 100% |
| fsk4_100 | hl_moderate | 10 dB | 50% | 95% | 60% | 80% | 90% |
| fsk4_50w | awgn | 20 dB | 50% | 95% | 55% | 100% | 100% |
| fsk4_50w | hl_moderate | 10 dB | 50% | 95% | 55% | 100% | 95% |
| fsk4_50w | hl_disturbed | 10 dB | 40% | 60% | 50% | 70% | 80% |

- **Receiver restart** (`run_mode(..., fsk_restart=True)`): after every decoded
  frame the receiver is opened again and the last frame length of audio is fed to
  it again, so a burst that has already started is not missed. This alone brings
  bursts up to the back-to-back results. No frame was decoded twice.
- **FSK preamble** (`fsk_preamble=True`, codec2's `freedv_rawdatapreambletx()`,
  all tones in turn): helps the receiver find the tones but not the timing
  lock, so on its own it adds little. It costs 37% more airtime (0.5 s for
  `data_vhf_1`, 1 s at 100 baud, 2 s at 50 baud), and on top of the restart it
  gave no consistent gain.

### Bursts with the receiver restart

`python tools/fading_measure.py --modes datac4 data_vhf_1 fsk4_100 fsk4_50 fsk4_50w
fsk2_100 fsk4_25 fsk4_100_long --restart --frames 10 --channels awgn mid_disturbed
hl_moderate hl_disturbed`, single-frame bursts with 500 ms gaps, 10 frames per cell:

#### `awgn`: no fading, noise only (Doppler spread 0 Hz, delay 0 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| data_vhf_1, restart | 176 | 30% | 80% | 100% | 100% | 100% | 100% | 100% |
| *fsk4_100* (500 Hz), restart | 88 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| *fsk4_50* (400 Hz), restart | 44 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| *fsk4_50w* (550 Hz), restart | 44 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| *fsk2_100* (400 Hz), restart | 44 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| *fsk4_25* (350 Hz), restart | 22 | 100% | 100% | 90% | 90% | 90% | 100% | 100% |
| *fsk4_100_long* (500 Hz), restart | 97 | 90% | 100% | 100% | 100% | 100% | 100% | 100% |

#### `mid_disturbed`: mid-latitude disturbed (codec2 MPP) (Doppler spread 1 Hz, delay 2 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 80% | 100% | 100% | 100% | 100% | 100% | 100% |
| data_vhf_1, restart | 176 | 0% | 40% | 80% | 100% | 100% | 100% | 100% |
| *fsk4_100* (500 Hz), restart | 88 | 40% | 80% | 100% | 100% | 100% | 100% | 100% |
| *fsk4_50* (400 Hz), restart | 44 | 60% | 70% | 100% | 100% | 80% | 80% | 90% |
| *fsk4_50w* (550 Hz), restart | 44 | 70% | 100% | 100% | 90% | 90% | 80% | 90% |
| *fsk2_100* (400 Hz), restart | 44 | 60% | 100% | 90% | 100% | 100% | 80% | 100% |
| *fsk4_25* (350 Hz), restart | 22 | 80% | 70% | 90% | 80% | 80% | 70% | 90% |
| *fsk4_100_long* (500 Hz), restart | 97 | 40% | 90% | 100% | 100% | 100% | 100% | 100% |

#### `hl_moderate`: high-latitude moderate, auroral (Doppler spread 10 Hz, delay 3 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_vhf_1, restart | 176 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| *fsk4_100* (500 Hz), restart | 88 | 0% | 100% | 100% | 90% | 100% | 100% | 100% |
| *fsk4_50* (400 Hz), restart | 44 | 60% | 90% | 100% | 80% | 100% | 100% | 70% |
| *fsk4_50w* (550 Hz), restart | 44 | 80% | 90% | 90% | 100% | 100% | 90% | 90% |
| *fsk2_100* (400 Hz), restart | 44 | 70% | 100% | 100% | 100% | 100% | 90% | 100% |
| *fsk4_25* (350 Hz), restart | 22 | 60% | 70% | 70% | 70% | 70% | 90% | 80% |
| *fsk4_100_long* (500 Hz), restart | 97 | 0% | 100% | 100% | 100% | 90% | 90% | 90% |

#### `hl_disturbed`: high-latitude disturbed, strong aurora (Doppler spread 30 Hz, delay 7 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_vhf_1, restart | 176 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| *fsk4_100* (500 Hz), restart | 88 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| *fsk4_50* (400 Hz), restart | 44 | 20% | 90% | 60% | 70% | 50% | 40% | 70% |
| *fsk4_50w* (550 Hz), restart | 44 | 40% | 60% | 30% | 70% | 50% | 40% | 30% |
| *fsk2_100* (400 Hz), restart | 44 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| *fsk4_25* (350 Hz), restart | 22 | 10% | 0% | 0% | 0% | 0% | 10% | 0% |
| *fsk4_100_long* (500 Hz), restart | 97 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |

With the restart, the FSK candidates reach in burst form what they reached back
to back in section 6: 90 to 100% on `hl_moderate` from 0 dB up for every
candidate except `fsk4_25`, and 40 to 90% for the 50 baud 4FSK candidates on
`hl_disturbed`.

### In FreeDATA

The demodulator does this now (`Demodulator.restart_fsk()`, see
[modem-audio-radio.md](modem-audio-radio.md), "FSK receiver restart"):

1. It keeps the last frame length of audio of each FSK mode, and after a
   decoded FSK frame opens the codec2 instance again and feeds that audio to it
   first.
2. `reset_data_sync()` marks the FSK modes, and their decoding threads open the
   instances again once transmitting has ended, since `freedv_set_sync()` does
   not reset them.

A cleaner fix would be in codec2: let `freedv_set_sync()` reset the FSK_LDPC
state, or let the receiver search for the unique word again right after a
decoded frame when it is set to one frame per burst.

The replay covers one frame length, as measured above. codec2 decodes a frame
between 0 and a little more than one frame length after its end, so when that
delay is long, the replay starts after the next burst has begun and that burst
is lost (one of eight bursts in `tests/test_demodulator_fsk.py`, and probably the
remaining 0 to 10% losses in the tables above). Replaying 1.5 frame lengths finds
those bursts too, but then decodes some frames twice, and filtering duplicates
must not swallow a genuine ARQ repeat, so it was left for later.

## 8. The HF FSK mode `data_fsk4_50`

The `fsk4_50w` candidate is now a FreeDATA mode, `FREEDV_MODE.data_fsk4_50`
(value 202):

| | |
|---|---|
| Modulation | 4FSK, 50 baud (20 ms symbols), 150 Hz tone spacing, tones 1275 to 1725 Hz |
| Bandwidth | about 550 Hz, FFT slot 3 like `datac4` and `data_ofdm_500` |
| LDPC code | `H_256_512_4` |
| Frame | 32 bytes (30 of payload, 2 of CRC), 5.44 s, no preamble or postamble |
| Payload rate | 44 bps; 34 bps as an ARQ burst (7 bytes of burst header) |

Where it is:

- `codec2.py`: the enum value, `data_fsk4_50_config` in `fsk_configurations`
  (which `open_instance()` opens with `freedv_open_advanced`), and
  `FREEDV_MODE_USED_SLOTS.data_fsk4_50`.
- `modulator.py`: its own TX instance. `create_burst()` sends it without
  codec2's FSK preamble whatever `EXP.enable_vhf` says: the preamble would add 2 s
  per frame and gave nothing on top of the receiver restart (section 7).
- `demodulator.py`: it is in `fsk_configurations`, so the receiver restart after
  every decoded frame and after transmitting applies. The mode needed one more
  change: its receiver asks for up to 8160 samples at once (`nin`, about one
  second; OFDM modes ask for under 1000), and the 9600-sample audio buffer
  could not take that plus an incoming block, so the modem would drop the block
  as overflow while the decoder waited for more audio, and decoding stopped. The
  buffer now holds `freedv_get_n_max_modem_samples()` plus a block.
- It is **not decoded by default** and not an ARQ speed level yet; to receive it,
  enable it with `set_decode_mode({FREEDV_MODE.data_fsk4_50.value: True})`.
- `tests/test_demodulator_fsk.py` (`TestHfFsk`) modulates bursts with the
  modulator and decodes them in the demodulator's decoding thread, on `awgn` at
  0 dB and on `hl_moderate`.

### Measurements

`python tools/fading_measure.py --modes datac4 signalling data_vhf_1 data_fsk4_50
--restart --stream --frames 20 --channels awgn mid_disturbed hl_moderate
hl_disturbed`, 20 frames per cell. "restart" rows are single-frame bursts with
500 ms gaps and the receiver restart, as the demodulator does it; "stream" rows
send the frames back to back.

#### `awgn`: no fading, noise only (Doppler spread 0 Hz, delay 0 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| signalling | 51 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| data_vhf_1, restart | 176 | 60% | 85% | 100% | 95% | 95% | 100% | 100% |
| data_vhf_1, stream | 176 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| data_fsk4_50, restart | 44 | 95% | 95% | 95% | 95% | 95% | 95% | 95% |
| data_fsk4_50, stream | 44 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |

#### `mid_disturbed`: mid-latitude disturbed (codec2 MPP) (Doppler spread 1 Hz, delay 2 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 65% | 95% | 100% | 100% | 100% | 100% | 90% |
| signalling | 51 | 65% | 100% | 95% | 100% | 100% | 100% | 100% |
| data_vhf_1, restart | 176 | 5% | 65% | 80% | 95% | 90% | 100% | 95% |
| data_vhf_1, stream | 176 | 15% | 75% | 95% | 95% | 90% | 95% | 100% |
| data_fsk4_50, restart | 44 | 85% | 85% | 95% | 85% | 90% | 95% | 95% |
| data_fsk4_50, stream | 44 | 85% | 100% | 95% | 95% | 100% | 100% | 100% |

#### `hl_moderate`: high-latitude moderate, auroral (Doppler spread 10 Hz, delay 3 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| signalling | 51 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_vhf_1, restart | 176 | 0% | 0% | 0% | 5% | 0% | 0% | 0% |
| data_vhf_1, stream | 176 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_fsk4_50, restart | 44 | 85% | 95% | 95% | 95% | 95% | 90% | 90% |
| data_fsk4_50, stream | 44 | 95% | 100% | 95% | 95% | 100% | 100% | 100% |

#### `hl_disturbed`: high-latitude disturbed, strong aurora (Doppler spread 30 Hz, delay 7 ms)

| mode | payload bps | -5 dB | 0 dB | 5 dB | 10 dB | 15 dB | 20 dB | 30 dB |
|---|---|---|---|---|---|---|---|---|
| **datac4** | 80 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| signalling | 51 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_vhf_1, restart | 176 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_vhf_1, stream | 176 | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| data_fsk4_50, restart | 44 | 30% | 50% | 45% | 55% | 65% | 65% | 55% |
| data_fsk4_50, stream | 44 | 60% | 85% | 70% | 70% | 70% | 70% | 80% |

- On the auroral presets `data_fsk4_50` is the only mode that gets through: 85 to
  95% of the bursts on `hl_moderate` and 30 to 65% on `hl_disturbed`, where
  `datac4` and the signalling modes decode nothing. This matches `fsk4_50w` in
  sections 6 and 7 (with 20 instead of 10 frames per cell).
- On `mid_disturbed` it decodes 85% of the bursts at -5 dB, where `datac4` decodes
  65%, but at about half the payload rate.
- In bursts one frame in 20 is lost at every SNR from -5 to 30 dB, even on
  `awgn`, and none is decoded twice. Since it does not depend on the SNR, this is
  the replay timing of section 7 ("In FreeDATA"), not noise: codec2 sometimes
  decodes a frame so late that the replayed frame length starts after the next
  burst has begun.

### Decoding delay

codec2's FSK receiver decodes a frame only after it has collected the frame
length that follows it (section 7). With 5.44 s frames that is a long time: a
single frame followed by noise was decoded **3.9 to 5.1 s after its end**,
depending on where it fell in the receiver's processing blocks (`datac4`: 0.06 s,
`data_vhf_1`: 0.3 to 1.3 s). The receiver needs that audio, so the IRS cannot
answer earlier, and the ISS has to wait for it (see below).

### Integrating it into ARQ: the next step

What it would take to use `data_fsk4_50` as the lowest ARQ speed level and for
signalling on disturbed paths:

1. **Speed level.** Add it below `datac4` in `ARQSession.SPEED_LEVEL_DICT`
   (bandwidth 550, `FREEDV_MODE_USED_SLOTS.data_fsk4_50`, `duration_per_frame`
   5.44). The speed level travels as a byte in the ARQ frames, so renumbering the
   levels needs a protocol version bump, or the mode gets a new number above the
   others with an explicit order list for stepping up and down.
2. **Timeouts (done for the ISS's own frames).** The fixed timeouts did not
   fit: `TIMEOUT_TRANSFER` (3.5 s) and `TIMEOUT_CONNECT_ACK` (4.5 s) ran out
   before the IRS had even decoded the frame (up to 5.1 s after its end, see
   above). `ARQSessionISS.get_response_timeout()` now adds
   `codec2.get_decode_delay()` of the mode the frame was sent in: one frame
   length for FSK modes (5.44 s for `data_fsk4_50`), 0 for OFDM modes, so the
   OFDM waits stay as they were. That is enough while the IRS answers in
   DATAC13/DATAC14. If the answers also go in `data_fsk4_50` (point 3), the wait
   has to add the answer's airtime and its decode delay too, about
   5.4 + 5.4 + 5.4 s, roughly 16 s plus PTT and audio latency per burst. A codec2
   change that lets the FSK_LDPC receiver decode as soon as a frame is complete
   would cut this by two frame lengths. The IRS's own timeouts
   (`TIMEOUT_CONNECT` 55 s, `TIMEOUT_DATA` 90 s) are long enough as they are.
3. **Signalling.** A data level alone is of no use on an auroral path, since
   `signalling` (DATAC13) and `signalling_ack` (DATAC14) fail there as well (from
   3 to 7 Hz of spread, section 3). Session open, info, ACK, NACK and stop
   frames (14 bytes, `LENGTH_SIG0_FRAME`) fit in a 30-byte FSK frame. The
   receiving side has to decode `data_fsk4_50` all the time (one more decoding
   thread, whose CPU load should be measured), and answer in the mode the request came
   in. Connecting could alternate DATAC13 and `data_fsk4_50` attempts, or fall
   back to FSK after the DATAC13 retries.
4. **Fallback from failures, not SNR alone.** `get_appropriate_speed_level()`
   picks the level from the SNR (`min_snr`), but an auroral path shows a good SNR
   and still defeats every OFDM mode. The ISS should drop to `data_fsk4_50`
   after failed bursts at `datac4` (`transmit_wait_and_retry()` already has a
   temporary fallback to level 0 after two failed transmissions, which could step
   down once more), and the IRS could use failed decodes with sync (rx status 10
   in the demodulator's `state_buffer`) as a sign of a fading channel.
5. **Lost bursts.** The remaining 5% of lost bursts (above) could be recovered by
   replaying 1.5 frame lengths. In ARQ the duplicates this produces are probably
   harmless: the IRS sets `received_bytes` back to the offset of a frame it did
   not expect and writes its data there again (`arq_session_irs.py`), so a
   duplicate rewrites the same bytes. Whether it then sends a second ACK that
   confuses the ISS has to be checked.
6. **Air tests.** All numbers here are from the simulator (section 5); the mode
   should be tried on a real high-latitude path before it becomes a default.

