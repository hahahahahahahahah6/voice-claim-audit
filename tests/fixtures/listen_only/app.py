import speech_recognition as sr
r = sr.Recognizer()
with sr.Microphone() as mic:
    audio = r.listen(mic)
