from whisper import load_model
m = load_model("base")
m.transcribe("a.wav")

