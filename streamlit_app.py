# app.py

import streamlit as st
from utils import recognize_speech_from_mic, ask_openai, synthesize_speech

st.title("🍽️ Restaurant Voice Assistant (PoC)")

if st.button("🎤 Start Talking"):
    with st.spinner("Listening..."):
        user_text = recognize_speech_from_mic()
        st.success(f"You said: {user_text}")

        with st.spinner("Thinking..."):
            response = ask_openai(user_text)
            st.info(f"Assistant: {response}")

        with st.spinner("Speaking..."):
            synthesize_speech(response)
