import whisper
whisper.load_model("base").transcribe("a.wav")
