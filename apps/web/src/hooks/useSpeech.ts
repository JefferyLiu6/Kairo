import { useCallback, useEffect, useRef, useState } from "react";

/** The parts of the browser's speech recognition this hook uses. TypeScript's DOM types do not cover it. */
type SpeechRecognizer = {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  continuous: boolean;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
};

type SpeechWindow = {
  SpeechRecognition?: new () => SpeechRecognizer;
  webkitSpeechRecognition?: new () => SpeechRecognizer;   // Safari and older Chrome
};

const speechWindow = window as unknown as SpeechWindow;
const SpeechRecognitionAPI = speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;

export function useSpeech() {
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [voiceEnabled, setVoiceEnabled] = useState(false);

  const recognitionRef = useRef<SpeechRecognizer | null>(null);
  const synth = window.speechSynthesis;
  // Always-current refs — no stale closure issues
  const voiceEnabledRef = useRef(false);
  const voicesRef = useRef<SpeechSynthesisVoice[]>([]);

  const supported = Boolean(SpeechRecognitionAPI) && "speechSynthesis" in window;

  // Load voices (Chrome fires voiceschanged; Safari returns them immediately)
  useEffect(() => {
    const load = () => { voicesRef.current = synth.getVoices(); };
    load();
    synth.addEventListener("voiceschanged", load);
    return () => synth.removeEventListener("voiceschanged", load);
  }, []);

  useEffect(() => {
    return () => {
      recognitionRef.current?.abort();
      synth.cancel();
    };
  }, []);

  const startListening = useCallback((onResult: (transcript: string) => void) => {
    if (!SpeechRecognitionAPI) return;
    synth.cancel();
    const rec = new SpeechRecognitionAPI();
    rec.lang = "en-US";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.continuous = false;
    rec.onresult = (e) => onResult(e.results[0][0].transcript);
    rec.onend = () => setIsListening(false);
    rec.onerror = () => setIsListening(false);
    recognitionRef.current = rec;
    rec.start();
    setIsListening(true);
  }, []);

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop();
    setIsListening(false);
  }, []);

  function _bestVoice(): SpeechSynthesisVoice | null {
    const voices = synth.getVoices();
    // Priority list: neural/natural first, then online, then any English
    const priority = [
      (v: SpeechSynthesisVoice) => /natural|neural/i.test(v.name) && v.lang.startsWith("en"),
      (v: SpeechSynthesisVoice) => v.name.toLowerCase().includes("online") && v.lang.startsWith("en"),
      (v: SpeechSynthesisVoice) => /aria|jenny|guy|emma|brian|amy/i.test(v.name),
      (v: SpeechSynthesisVoice) => v.lang.startsWith("en-US"),
      (v: SpeechSynthesisVoice) => v.lang.startsWith("en"),
    ];
    for (const match of priority) {
      const found = voices.find(match);
      if (found) return found;
    }
    return null;
  }

  function _doSpeak(text: string) {
    const clean = text
      .replace(/```[\s\S]*?```/g, "code block.")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .replace(/[*_#~>]/g, "")
      .replace(/\n+/g, " ")
      .trim();
    if (!clean) return;
    synth.cancel();
    const u = new SpeechSynthesisUtterance(clean);
    const voice = _bestVoice();
    if (voice) u.voice = voice;
    u.rate = 1.0;
    u.pitch = 1.0;
    u.onstart = () => setIsSpeaking(true);
    u.onend = () => setIsSpeaking(false);
    u.onerror = (e) => { console.warn("[TTS]", e.error); setIsSpeaking(false); };
    synth.speak(u);
  }

  const speak = useCallback((text: string) => {
    if (!voiceEnabledRef.current) return;
    _doSpeak(text);
  }, []);

  const speakDirect = useCallback((text: string) => {
    _doSpeak(text);
  }, []);

  const stopSpeaking = useCallback(() => {
    synth.cancel();
    setIsSpeaking(false);
  }, []);

  const toggleVoice = useCallback(() => {
    setVoiceEnabled((v) => {
      const next = !v;
      voiceEnabledRef.current = next;
      if (!next) synth.cancel();
      return next;
    });
  }, []);

  return {
    supported,
    isListening,
    isSpeaking,
    voiceEnabled,
    startListening,
    stopListening,
    speak,
    speakDirect,
    stopSpeaking,
    toggleVoice,
  };
}
