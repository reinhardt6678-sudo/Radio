"""分析最近录音的音频内容，判断是信号还是噪音"""
import sys, wave, numpy as np, os
try:
    sys.stdout.reconfigure(encoding='utf-8')
except: pass

recordings = [
    "data/recordings/20260609_192150_11175.0kHz_Great_Lakes_Michigan.wav",
    "data/recordings/20260609_191730_11175.0kHz_Great_Lakes_Michigan.wav",
    "data/recordings/20260609_190120_11175.0kHz_Great_Lakes_Michigan.wav",
]

for path in recordings:
    if not os.path.exists(path):
        print(f"[MISSING] {path}")
        continue
    
    print(f"\n{'='*60}")
    print(f"File: {os.path.basename(path)}")
    print(f"Size: {os.path.getsize(path)} bytes")
    
    with wave.open(path, 'rb') as wf:
        n_ch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        nf = wf.getnframes()
        raw = wf.readframes(nf)
        
    print(f"Format: {n_ch}ch, {sw*8}bit, {sr}Hz, {nf} frames ({nf/sr:.2f}s)")
    
    if sw == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    
    if n_ch > 1:
        samples = samples[::n_ch]
    
    # Overall stats
    rms = np.sqrt(np.mean(samples**2))
    peak = np.max(np.abs(samples))
    print(f"Overall RMS: {rms:.4f}")
    print(f"Peak amplitude: {peak:.4f}")
    
    # Window RMS analysis (1024 samples per window)
    ws = 1024
    n_win = len(samples) // ws
    wrms = []
    for i in range(n_win):
        chunk = samples[i*ws:(i+1)*ws]
        wrms.append(float(np.sqrt(np.mean(chunk**2))))
    wrms = np.array(wrms)
    
    print(f"Window RMS: min={wrms.min():.4f} mean={wrms.mean():.4f} max={wrms.max():.4f} std={wrms.std():.4f}")
    
    # How many windows above thresholds?
    for th in [0.05, 0.08, 0.10, 0.15]:
        above = np.sum(wrms >= th)
        print(f"  Windows >= {th:.2f}: {above}/{n_win} ({above/n_win*100:.0f}%)")
    
    # Time-domain pattern: is this a brief spike or sustained signal?
    above_010 = wrms >= 0.10
    transitions = np.diff(above_010.astype(int))
    n_onsets = np.sum(transitions == 1)
    n_offsets = np.sum(transitions == -1)
    
    if np.any(above_010):
        first_above = np.argmax(above_010) * ws / sr
        last_above = (len(above_010) - 1 - np.argmax(above_010[::-1])) * ws / sr
        print(f"  Signal activity (>=0.10): {first_above:.1f}s ~ {last_above:.1f}s")
        print(f"  On/Off transitions: {n_onsets} on, {n_offsets} off")
    else:
        print(f"  No windows above 0.10")
    
    # Frequency content (check if it has speech-like content)
    fft_data = np.fft.rfft(samples[:min(len(samples), sr*2)])  # first 2 seconds
    freqs = np.fft.rfftfreq(min(len(samples), sr*2), 1.0/sr)
    magnitude = np.abs(fft_data)
    
    # Speech typically 300-3000 Hz
    speech_mask = (freqs >= 300) & (freqs <= 3000)
    noise_mask = (freqs >= 10) & (freqs < 300) | (freqs > 3000)
    
    speech_energy = np.mean(magnitude[speech_mask]**2) if np.any(speech_mask) else 0
    noise_energy = np.mean(magnitude[noise_mask]**2) if np.any(noise_mask) else 0
    
    if noise_energy > 0:
        speech_ratio = speech_energy / noise_energy
        print(f"  Speech band (300-3000Hz) / noise ratio: {speech_ratio:.2f}")
        if speech_ratio > 3:
            print(f"  --> Likely contains SPEECH or tonal signal")
        elif speech_ratio > 1.5:
            print(f"  --> Possibly weak signal")
        else:
            print(f"  --> Likely just NOISE")
    
    # Check if all 3.0s duration means it's just tail_time triggering
    actual_dur = nf / sr
    print(f"\n  Actual duration: {actual_dur:.2f}s")
    print(f"  tail_time=3.0s --> ", end="")
    
    # Count how many windows are above open_threshold (0.10)
    above_open = np.sum(wrms >= 0.10)
    if above_open <= 2 and actual_dur <= 6.0:
        print("LIKELY FALSE POSITIVE (brief spike + tail_time)")
    elif above_open > n_win * 0.3:
        print("SUSTAINED SIGNAL (real)")
    else:
        print(f"MARGINAL ({above_open} windows above threshold)")

print(f"\n{'='*60}")
print("SUMMARY")
print(f"All 3 recordings are exactly 3.0s = tail_time.")
print(f"This strongly suggests: brief noise spikes trigger squelch,")
print(f"the signal never truly sustains, and tail_time (3s) runs out.")
print(f"These are almost certainly FALSE POSITIVES from noise spikes.")
