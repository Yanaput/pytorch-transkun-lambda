from .Data import writeMidi
import torch
import moduleconf
import numpy as np
from .Util import computeParamSize


def readAudio(path, normalize=True):
    import pydub
    audio = pydub.AudioSegment.from_mp3(path)
    y = np.array(audio.get_array_of_samples())
    y = y.reshape(-1, audio.channels)
    if normalize:
        y = np.float32(y) / 2 ** 15
    return audio.frame_rate, y


def transcribe(audioPath, outPath, device="cpu", segmentHopSize=None, segmentSize=None):

    import os
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # Get the directory of the current script
    weightPath = os.path.join(BASE_DIR, "pretrained/2.0.pt")
    confPath = os.path.join(BASE_DIR, "pretrained/2.0.conf")

    if not os.path.exists(audioPath):
        raise FileNotFoundError(f"Audio file not found")

    if not os.path.exists(weightPath):
        raise FileNotFoundError(f"Model weight file not found")

    if not os.path.exists(confPath):
        raise FileNotFoundError(f"Model configuration file not found")

    confManager = moduleconf.parseFromFile(confPath)
    TransKun = confManager["Model"].module.TransKun
    conf = confManager["Model"].config

    checkpoint = torch.load(weightPath, map_location="cpu")

    model = TransKun(conf=conf).to(device)

    if not "best_state_dict" in checkpoint:
        model.load_state_dict(checkpoint["state_dict"], strict=False)
    else:
        model.load_state_dict(checkpoint["best_state_dict"], strict=False)

    model.eval()

    # audioPath = args.audioPath
    # outPath = args.outPath
    torch.set_grad_enabled(False)

    fs, audio = readAudio(audioPath)

    if fs != model.fs:
        import soxr
        audio = soxr.resample(
            audio,  # 1D(mono) or 2D(frames, channels) array input
            fs,  # input samplerate
            model.fs  # target samplerate
        )

    x = torch.from_numpy(audio).to(device)

    notesEst = model.transcribe(x, stepInSecond=segmentHopSize, segmentSizeInSecond=segmentSize,
                                discardSecondHalf=False)

    try:
        outputMidi = writeMidi(notesEst)
        outputMidi.write(outPath)
    except Exception as e:
        raise RuntimeError(f"Failed to write MIDI file: {str(e)}")
