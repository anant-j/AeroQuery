"use client";

import React, { useState, useRef, useCallback, useEffect, useSyncExternalStore } from "react";
import ReactMarkdown from "react-markdown";
import type { MLCEngine } from "@mlc-ai/web-llm";
import { EVAL_DATA, formatComparisonMessage, getTurnstileSiteKey, METRIC_INSIGHTS, METRIC_LABELS, SESSION_LIMIT_EVENT, SYSTEM_PROMPT, WEBLLM_MODEL } from "./lib/constants.js";
import { consumeSessionRequest, remainingSessionRequests } from "./lib/session-limit.js";
import { streamComparison } from "./lib/query-gateway.js";
import { createWorkerSession } from "./lib/worker-session.js";

const TURNSTILE_SITE_KEY = getTurnstileSiteKey();
const WORKER_URL = (process.env.NEXT_PUBLIC_CLOUDFLARE_WORKER_API_URL || "").replace(/\/+$/, "");
const WORKER_KEY = process.env.NEXT_PUBLIC_CLOUDFLARE_WORKER_KEY || "";
const DefaultQuery = "How long do I need to wait after drinking alcohol before piloting an aircraft?";
const THEME_CHANGE_EVENT = "aeroquery-theme-change";

function getRemainingRequests() {
  try {
    return remainingSessionRequests();
  } catch {
    return 0;
  }
}

function subscribeToSessionRequests(callback: () => void) {
  window.addEventListener(SESSION_LIMIT_EVENT, callback);
  window.addEventListener("storage", callback);
  return () => {
    window.removeEventListener(SESSION_LIMIT_EVENT, callback);
    window.removeEventListener("storage", callback);
  };
}

function getThemeSnapshot() {
  return document.documentElement.classList.contains("dark");
}

function subscribeToTheme(callback: () => void) {
  window.addEventListener(THEME_CHANGE_EVENT, callback);
  return () => window.removeEventListener(THEME_CHANGE_EVENT, callback);
}

function reserveSessionRequest() {
  let accepted = false;
  try {
    accepted = consumeSessionRequest();
  } catch {
    accepted = false;
  }
  window.dispatchEvent(new Event(SESSION_LIMIT_EVENT));
  if (!accepted) throw new Error("Session request limit reached or session storage is unavailable.");
}

declare global {
  interface Window {
    turnstile?: {
      render: (container: HTMLElement, options: Record<string, unknown>) => string;
      execute: (widget: string) => void;
      remove: (widget: string) => void;
    };
  }
}

type ModelOption = "openai" | "webllm";

interface Source {
  section: string;
  title: string;
  text?: string;
}

interface AskResponse {
  query: string;
  answer: string;
  model: string;
  tokens: number;
  use_rag: boolean;
  sources?: Source[];
  agent?: {
    query_type?: string;
    sub_queries?: string[];
    context_sufficient?: boolean;
    steps?: string[];
  };
}

interface Chunk {
  section: string;
  title: string;
  text: string;
}

function SourceItem({ source }: { source: Source }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div>
      <button
        onClick={() => source.text && setExpanded(!expanded)}
        className={`text-xs px-2 py-0.5 rounded inline-flex items-center gap-1 ${source.text ? "cursor-pointer hover:bg-gray-200 dark:hover:bg-gray-700" : "cursor-default"} bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400`}
      >
        §{source.section}
        {source.text && (
          <svg className={`w-3 h-3 transition-transform ${expanded ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        )}
      </button>
      {expanded && source.text && (
        <div className="mt-1 ml-1 px-3 py-2 text-xs text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-800/50 rounded border border-gray-100 dark:border-gray-700 whitespace-pre-wrap max-h-48 overflow-y-auto">
          {source.text}
        </div>
      )}
    </div>
  );
}

function ResultCard({ result, label, accent, streaming }: { result: AskResponse; label: string; accent: string; streaming?: boolean }) {
  return (
    <div className={`border rounded-lg p-5 ${accent}`}>
      <div className="flex items-center gap-2 mb-3">
        <span className={`text-xs px-2 py-1 rounded font-medium ${label === "RAG" ? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300" : "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300"}`}>
          {label}
        </span>
        <span className="text-xs text-gray-500 dark:text-gray-400">
          {result.model}{result.tokens > 0 ? ` · ${result.tokens} tokens` : ""}
        </span>
      </div>

      <div className="prose prose-sm prose-gray dark:prose-invert max-w-none mb-3">
        {streaming && !result.answer && (
          <span className="text-sm text-gray-400 dark:text-gray-500 animate-pulse">
            {label === "RAG" ? "Retrieving context..." : "Generating..."}
          </span>
        )}
        <ReactMarkdown>{result.answer}</ReactMarkdown>
        {streaming && result.answer && (
          <span className="inline-block w-1.5 h-4 bg-gray-400 dark:bg-gray-500 animate-pulse ml-0.5 align-text-bottom rounded-sm" />
        )}
      </div>

      {result.sources && result.sources.length > 0 && (
        <div className="border-t border-gray-100 dark:border-gray-700 pt-3 mt-3">
          <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2">Sources</p>
          <div className="flex flex-col gap-1.5">
            {result.sources.map((s, i) => (
              <SourceItem key={i} source={s} />
            ))}
          </div>
        </div>
      )}

      {result.agent && result.agent.steps && result.agent.steps.length > 0 && (
        <div className="border-t border-gray-100 dark:border-gray-700 pt-3 mt-3">
          <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">Agent Trace</p>
          <div className="flex items-center gap-1 flex-wrap">
            {result.agent.steps.map((step, i) => (
              <span key={i} className="flex items-center gap-1">
                <span className="text-[10px] font-mono bg-blue-50 dark:bg-blue-950/40 text-blue-600 dark:text-blue-400 px-1.5 py-0.5 rounded">
                  {step}
                </span>
                {i < result.agent!.steps!.length - 1 && (
                  <span className="text-gray-300 dark:text-gray-600 text-[10px]">→</span>
                )}
              </span>
            ))}
          </div>
          {result.agent.sub_queries && result.agent.sub_queries.length > 0 && (
            <div className="mt-1.5">
              <p className="text-[10px] text-gray-400 dark:text-gray-500">Sub-queries:</p>
              {result.agent.sub_queries.map((sq, i) => (
                <p key={i} className="text-[10px] text-gray-500 dark:text-gray-400 ml-2">• {sq}</p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [modelOption, setModelOption] = useState<ModelOption>("openai");
  const [loading, setLoading] = useState(false);
  const [ragResult, setRagResult] = useState<AskResponse | null>(null);
  const [bareResult, setBareResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState("");
  const [ragStreaming, setRagStreaming] = useState(false);
  const [bareStreaming, setBareStreaming] = useState(false);
  const remainingRequests = useSyncExternalStore(subscribeToSessionRequests, getRemainingRequests, () => 0);
  const requestsNeeded = modelOption === "openai" ? 2 : 1;

  // WebLLM state
  const [webllmReady, setWebllmReady] = useState(false);
  const [webllmProgress, setWebllmProgress] = useState(0);
  const [webllmStatus, setWebllmStatus] = useState("Initializing...");
  const [webllmLoading, setWebllmLoading] = useState(false);
  const [webllmError, setWebllmError] = useState("");
  const engineRef = useRef<MLCEngine | null>(null);
  const webllmStartedRef = useRef(false);
  const turnstileContainer = useRef<HTMLDivElement>(null);
  const [turnstileReady, setTurnstileReady] = useState(false);
  const sessionRef = useRef<ReturnType<typeof createWorkerSession> | null>(null);

  useEffect(() => {
    const script = document.getElementById("turnstile-script");
    const onLoad = () => setTurnstileReady(Boolean(window.turnstile));
    const onError = () => setError("Verification could not load. Please refresh and try again. If this still does not work, please reach out to me at anant.j2409@gmail.com");
    onLoad();
    script?.addEventListener("load", onLoad);
    script?.addEventListener("error", onError);
    return () => {
      script?.removeEventListener("load", onLoad);
      script?.removeEventListener("error", onError);
    };
  }, []);

  const getTurnstileToken = useCallback(async () => {
    const turnstile = window.turnstile;
    const container = turnstileContainer.current;
    if (!turnstile || !container || !TURNSTILE_SITE_KEY) throw new Error("Verification is unavailable.");
    let widget = "";
    let timer: ReturnType<typeof setTimeout>;
    try {
      return await new Promise<string>((resolve, reject) => {
        timer = setTimeout(() => reject(new Error("Verification timed out. Please try again.")), 120000);
        widget = turnstile.render(container, {
          sitekey: TURNSTILE_SITE_KEY,
          action: "aeroquery",
          execution: "execute",
          appearance: "interaction-only",
          callback: resolve,
          "error-callback": () => reject(new Error("Verification failed. Please try again.")),
          "expired-callback": () => reject(new Error("Verification expired. Please try again.")),
        });
        turnstile.execute(widget);
      });
    } finally {
      clearTimeout(timer!);
      if (widget) turnstile.remove(widget);
    }
  }, []);

  useEffect(() => {
    if (!turnstileReady || !TURNSTILE_SITE_KEY) return;
    sessionRef.current ??= createWorkerSession(WORKER_URL, WORKER_KEY, getTurnstileToken);
    sessionRef.current().catch((error: unknown) => {
      setError(error instanceof Error ? error.message : "Verification failed. Please try again.");
    });
  }, [turnstileReady, getTurnstileToken]);

  const startWebLLM = useCallback(async () => {
    if (webllmStartedRef.current || webllmReady) return;
    webllmStartedRef.current = true;
    setWebllmError("");
    setWebllmLoading(true);

    try {
      if (!("gpu" in navigator)) {
        throw new Error("WebGPU is not available in this browser.");
      }
      const { CreateMLCEngine } = await import("@mlc-ai/web-llm");
      const engine = await CreateMLCEngine(WEBLLM_MODEL, {
        initProgressCallback: (progress) => {
          setWebllmProgress(Math.round(progress.progress * 100));
          setWebllmStatus(progress.text);
        },
      });
      engineRef.current = engine;
      setWebllmReady(true);
      setWebllmLoading(false);
    } catch (error) {
      console.error("WebLLM initialization failed", error);
      setWebllmError(error instanceof Error ? error.message : "Unknown WebLLM error");
      setWebllmStatus("Load failed");
      setWebllmLoading(false);
      webllmStartedRef.current = false;
    }
  }, [webllmReady]);

  async function readComparisonStream(query: string, sessionToken: string) {
    // Keep the existing session budget: a comparison uses two backend requests.
    reserveSessionRequest();
    reserveSessionRequest();
    const reader = streamComparison(WORKER_URL, WORKER_KEY, sessionToken, query, new AbortController().signal).getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const upstreamBuffers = { rag: "", bare: "" };
    const completed = { rag: false, bare: false };
    let tokensSincePaint = 0;

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            const event = JSON.parse(line.slice(6));
            if (event.mode !== "rag" && event.mode !== "bare") throw new Error("Invalid stream response");
            const mode: "rag" | "bare" = event.mode;
            if (event.error) throw new Error(`${mode === "rag" ? "RAG" : "Bare"}: ${event.error}`);
            if (typeof event.chunk !== "string") throw new Error("Invalid stream response");
            upstreamBuffers[mode] += event.chunk;
            const upstreamLines = upstreamBuffers[mode].split("\n");
            upstreamBuffers[mode] = upstreamLines.pop() || "";
            for (const upstreamLine of upstreamLines) {
              if (!upstreamLine.startsWith("data: ")) continue;
              const data = JSON.parse(upstreamLine.slice(6));
              const update = mode === "rag" ? setRagResult : setBareResult;
              if (data.type === "token") {
                update(prev => prev ? { ...prev, answer: prev.answer + data.token } : null);
                tokensSincePaint += 1;
                if (tokensSincePaint >= 8) {
                  tokensSincePaint = 0;
                  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
                }
              } else if (data.type === "sources" && mode === "rag") {
                setRagResult(prev => prev ? { ...prev, sources: data.sources, agent: data.agent } : null);
              } else if (data.type === "usage") {
                update(prev => prev ? { ...prev, tokens: data.tokens, model: data.model } : null);
              } else if (data.type === "done") {
                completed[mode] = true;
                if (mode === "rag") setRagStreaming(false);
                else setBareStreaming(false);
              }
            }
          }
        }
      }
      if (!completed.rag || !completed.bare) throw new Error("Generation stream ended unexpectedly");
    } finally {
      await reader.cancel().catch(() => {});
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (loading || getRemainingRequests() < requestsNeeded) {
      setError("You have reached the session limit. Please try again in a few minutes.");
      return;
    }
    const effectiveQuery = query.trim() || DefaultQuery;

    setLoading(true);
    setError("");
    setRagResult(null);
    setBareResult(null);
    setRagStreaming(false);
    setBareStreaming(false);

    try {
      if (!sessionRef.current) throw new Error("Verification is unavailable.");
      const sessionToken = await sessionRef.current();
      if (modelOption === "openai") {
        // Initialize results with empty answers for streaming
        setRagResult({ query: effectiveQuery, answer: "", model: "", tokens: 0, use_rag: true, sources: [] });
        setBareResult({ query: effectiveQuery, answer: "", model: "", tokens: 0, use_rag: false });
        setRagStreaming(true);
        setBareStreaming(true);

        await readComparisonStream(effectiveQuery, sessionToken);
      } else {
        // WebLLM: retrieve chunks from server, generate locally with streaming
        reserveSessionRequest();
        const retrieveRes = await fetch(`${WORKER_URL}/api/retrieve`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "x-aeroquery-key": WORKER_KEY, "Authorization": `Bearer ${sessionToken}` },
          body: JSON.stringify({ query: effectiveQuery }),
        });

        if (!retrieveRes.ok) {
          const error = await retrieveRes.json().catch(() => null);
          throw new Error(error?.error || "Retrieval API error");
        }
        const retrieveData = await retrieveRes.json();
        const chunks: Chunk[] = retrieveData.chunks;

        const ragMessages = [
          { role: "system" as const, content: SYSTEM_PROMPT },
          { role: "user" as const, content: formatComparisonMessage(effectiveQuery, chunks) },
        ];
        const bareMessages = [
          { role: "system" as const, content: SYSTEM_PROMPT },
          { role: "user" as const, content: formatComparisonMessage(effectiveQuery, null) },
        ];

        const webllmModel = `WebLLM (${WEBLLM_MODEL})`;

        // RAG with streaming (WebLLM is single-threaded, run sequentially)
        setRagResult({
          query: effectiveQuery, answer: "", model: webllmModel, tokens: 0, use_rag: true,
          sources: chunks.map((c) => ({ section: c.section, title: c.title, text: c.text })),
        });
        setRagStreaming(true);

        const ragStream = await engineRef.current!.chat.completions.create({
          messages: ragMessages,
          temperature: 0,
          stream: true,
        });
        for await (const chunk of ragStream) {
          const token = chunk.choices[0]?.delta?.content;
          if (token) {
            setRagResult(prev => prev ? { ...prev, answer: prev.answer + token } : null);
          }
        }
        setRagStreaming(false);

        // Bare LLM with streaming
        setBareResult({ query: effectiveQuery, answer: "", model: webllmModel, tokens: 0, use_rag: false });
        setBareStreaming(true);
        const bareStream = await engineRef.current!.chat.completions.create({
          messages: bareMessages,
          temperature: 0,
          stream: true,
        });
        for await (const chunk of bareStream) {
          const token = chunk.choices[0]?.delta?.content;
          if (token) {
            setBareResult(prev => prev ? { ...prev, answer: prev.answer + token } : null);
          }
        }
        setBareStreaming(false);
      }
    } catch (err) {
      setError("Something went wrong. Please try again later.");
      if(err instanceof Error){
        console.error(err.message);
      }
    } finally {
      setLoading(false);
      setRagStreaming(false);
      setBareStreaming(false);
    }
  };

  const isDark = useSyncExternalStore(subscribeToTheme, getThemeSnapshot, () => false);

  const toggleTheme = () => {
    const next = !isDark;
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
    window.dispatchEvent(new Event(THEME_CHANGE_EVENT));
  };

  return (
    <main className="w-full max-w-[1400px] mx-auto px-8 py-12">
      <div className="flex items-start justify-between mb-10">
        <div>
          <h1 className="text-3xl font-bold text-gray-800 dark:text-gray-100 mb-2">AeroQuery</h1>
          <p className="text-gray-500 dark:text-gray-400">
            Ask questions about Canadian aviation regulations. Powered by RAG over Transport Canada Canadian Aviation Regulations (CARs).
          </p>
        </div>
        <button
          onClick={toggleTheme}
          className="p-2 rounded-lg border border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
          aria-label="Toggle theme"
        >
          {isDark ? (
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" /></svg>
          ) : (
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" /></svg>
          )}
        </button>
      </div>

      <div
        role="alert"
        className="flex items-start gap-2 mb-8 rounded-lg border border-amber-300 dark:border-amber-700/60 bg-amber-50 dark:bg-amber-900/20 px-4 py-3 text-xs text-amber-800 dark:text-amber-200"
      >
        <svg className="w-4 h-4 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
        </svg>
        <p>
          <strong className="font-semibold">For demonstration purposes only.</strong>{" "}
          AI-generated answers can be incomplete or wrong. Always cross-verify every
          response against the official Transport Canada CAR/AIM and other authoritative
          sources before relying on it. This tool is provided &ldquo;as is&rdquo; and assumes
          no responsibility or liability for any decisions made or outcomes resulting
          from its use.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="mb-8">
        <div ref={turnstileContainer} />
        {!TURNSTILE_SITE_KEY && <p role="alert" className="mb-3 text-sm text-red-600">Verification is not configured.</p>}
        <div className="flex gap-3 mb-3">
          <input
            name="questionBox"
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={DefaultQuery}
            aria-label="Search Canadian aviation regulations"
            className="flex-1 border border-gray-200 dark:border-gray-700 rounded-lg px-4 py-3 text-sm text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-900 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            type="submit"
            disabled={loading || !TURNSTILE_SITE_KEY || !turnstileReady || remainingRequests < requestsNeeded || (modelOption === "webllm" && !webllmReady)}
            className="bg-blue-600 text-white px-6 py-3 rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "Searching..." : "Compare"}
          </button>
        </div>

        <div className="flex items-center gap-4">
          <label htmlFor="model-select" className="text-xs text-gray-500 dark:text-gray-400">Model:</label>
          <select
            id="model-select"
            value={modelOption}
            onChange={(e) => setModelOption(e.target.value as ModelOption)}
            className="text-xs border border-gray-200 dark:border-gray-700 rounded px-2 py-1 bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-200"
          >
            <option value="openai">Azure OpenAI (GPT-5.4-mini)</option>
            <option value="webllm" disabled={!webllmReady}>WebLLM (Llama 3.2 1B)</option>
          </select>
          {!webllmReady && (
            <button
              type="button"
              onClick={startWebLLM}
              disabled={webllmLoading}
              className="text-xs border border-gray-200 dark:border-gray-700 rounded px-2 py-1 text-gray-700 dark:text-gray-200 disabled:opacity-50"
            >
              {webllmLoading ? `Loading ${webllmProgress}%` : webllmError ? "Retry WebLLM" : "Load WebLLM"}
            </button>
          )}
          {webllmLoading && (
            <span className="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1">
              <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24" aria-hidden="true">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              <span aria-live="polite">{webllmStatus.length > 50 ? webllmStatus.slice(0, 50) + "..." : webllmStatus}</span>
            </span>
          )}
          {webllmError && (
            <span role="alert" className="text-xs text-red-600 dark:text-red-400">
              {webllmError}
            </span>
          )}
          {webllmReady && modelOption === "webllm" && (
            <span className="text-xs text-green-500">Ready — runs in your browser</span>
          )}
        </div>

        <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
          Runs the same question with and without RAG so you can compare the answers side by side.
        </p>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-2" aria-live="polite">
          {remainingRequests < requestsNeeded && " You have reached the session limits. Please try again later."}
        </p>
      </form>

      {error && (
        <div role="alert" className="bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 px-4 py-3 rounded-lg mb-6 text-sm">
          {error}
        </div>
      )}

      {(ragResult || bareResult) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-10">
          {ragResult && (
            <ResultCard result={ragResult} label="RAG" accent="border-green-200 dark:border-green-800 bg-green-50/30 dark:bg-green-950/30" streaming={ragStreaming} />
          )}
          {bareResult && (
            <ResultCard result={bareResult} label="Bare LLM" accent="border-yellow-200 dark:border-yellow-800 bg-yellow-50/30 dark:bg-yellow-950/30" streaming={bareStreaming} />
          )}
        </div>
      )}

      <div className="border-t border-gray-100 dark:border-gray-800 pt-8">
        <h2 className="text-lg font-semibold text-gray-700 dark:text-gray-200 mb-2">Eval Benchmark (RAGAS v0.4)</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
          Exploratory historical run · 50 questions · 3 models · single GPT-5.4-mini judge
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 dark:border-gray-700">
                <th className="text-left py-2 pr-3 font-medium text-gray-500 dark:text-gray-400">Metric</th>
                {EVAL_DATA.map((row, i) => (
                  <th key={i} colSpan={3} className="text-center py-2 px-1 font-medium text-gray-500 dark:text-gray-400 border-l border-gray-100 dark:border-gray-800">
                    {row.model}
                  </th>
                ))}
              </tr>
              <tr className="border-b border-gray-100 dark:border-gray-800">
                <th></th>
                {EVAL_DATA.map((_, i) => (
                  <React.Fragment key={i}>
                    <th className="text-center py-1 px-1 font-normal text-gray-500 dark:text-gray-400 border-l border-gray-100 dark:border-gray-800">Bare</th>
                    <th className="text-center py-1 px-1 font-normal text-gray-500 dark:text-gray-400">RAG</th>
                    <th className="text-center py-1 px-1 font-normal text-gray-500 dark:text-gray-400">+Rerank</th>
                  </React.Fragment>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.keys(METRIC_LABELS).map((metric) => {
                return (
                <React.Fragment key={metric}>
                <tr className="border-b border-gray-50 dark:border-gray-800">
                  <td className="py-2 pr-3 text-gray-700 dark:text-gray-300 whitespace-nowrap font-medium">{METRIC_LABELS[metric]}</td>
                  {EVAL_DATA.map((row, i) => {
                    const m = row.metrics[metric as keyof typeof row.metrics];
                    const bare = "bare" in m ? m.bare : null;
                    const rag = "rag" in m ? m.rag : null;
                    const rerank = "rerank" in m ? m.rerank : null;
                    const vals = [bare, rag, rerank].filter((v) => v !== null && v !== undefined) as number[];
                    const maxVal = vals.length > 0 ? Math.max(...vals) : 0;
                    const minVal = vals.length > 0 ? Math.min(...vals) : 0;
                    const delta = minVal > 0 ? Math.round(((maxVal - minVal) / minVal) * 100) : 0;

                    const cellClass = (val: number | null | undefined, isFirst: boolean, isBare: boolean) => {
                      const border = isFirst ? " border-l border-gray-100 dark:border-gray-800" : "";
                      if (val === null || val === undefined) return `text-gray-400 dark:text-gray-600${border}`;
                      if (val === maxVal && vals.length > 1) return `text-gray-800 dark:text-gray-100 font-bold${border}`;
                      if (!isBare && val === minVal && vals.length > 1 && bare !== null && bare !== undefined && val < bare) return `text-gray-800 dark:text-gray-100 font-bold${border}`;
                      return `text-gray-600 dark:text-gray-400${border}`;
                    };

                    const cellBg = (val: number | null | undefined, isBare: boolean): string | undefined => {
                      if (val === null || val === undefined) return undefined;
                      if (val === maxVal && vals.length > 1) return isDark ? "rgba(16, 185, 129, 0.15)" : "rgba(16, 185, 129, 0.08)";
                      if (!isBare && val === minVal && vals.length > 1 && bare !== null && bare !== undefined && val < bare) return isDark ? "rgba(239, 68, 68, 0.15)" : "rgba(239, 68, 68, 0.08)";
                      return undefined;
                    };

                    const renderCell = (val: number | null | undefined, isFirst: boolean, isBare: boolean) => {
                      if (val === null || val === undefined) return <td className={`text-center py-2 px-2 font-mono ${cellClass(val, isFirst, isBare)}`}>—</td>;
                      const isMax = val === maxVal && vals.length > 1;
                      const isRegression = !isBare && bare !== null && bare !== undefined && val < bare;
                      const regressionDelta = isRegression && bare ? Math.round(((bare - val) / bare) * 100) : 0;
                      const bg = cellBg(val, isBare);
                      return (
                        <td className={`text-center py-2 px-2 font-mono ${cellClass(val, isFirst, isBare)}`} style={bg ? { background: bg } : undefined}>
                          {val.toFixed(2)}
                          {isMax && delta > 0 && (
                            <span className="text-emerald-600 dark:text-emerald-400 font-semibold ml-1">↑{delta}%</span>
                          )}
                          {isRegression && regressionDelta > 0 && (
                            <span className="text-red-500 dark:text-red-400 font-semibold ml-1">↓{regressionDelta}%</span>
                          )}
                        </td>
                      );
                    };

                    return (
                      <React.Fragment key={i}>
                        {renderCell(bare, true, true)}
                        {renderCell(rag, false, false)}
                        {renderCell(rerank, false, false)}
                      </React.Fragment>
                    );
                  })}
                </tr>
                <tr className="border-b border-gray-100 dark:border-gray-800">
                  <td colSpan={1 + EVAL_DATA.length * 3} className="py-1.5 px-1 text-xs text-gray-500 dark:text-gray-400 italic">
                    {METRIC_INSIGHTS[metric]}
                  </td>
                </tr>
                </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>

        <p className="text-xs text-gray-500 dark:text-gray-400 mt-4">
          <span className="text-emerald-600 dark:text-emerald-400">↑ Green</span> = highest observed score. <span className="text-red-500 dark:text-red-400">↓ Red</span> = lower than the bare baseline. These descriptive comparisons are not statistical significance tests.
        </p>
      </div>

      <div className="border-t border-gray-100 dark:border-gray-800 pt-6 mt-8">
        <h2 className="text-sm font-semibold text-gray-500 dark:text-gray-400 mb-3">Built With</h2>
        {[
          { label: "AI", items: [
            { name: "Azure AI Foundry", desc: "Azure OpenAI models, embeddings & judge", url: "https://ai.azure.com" },
            { name: "Ollama", desc: "Local evaluation model", url: "https://ollama.com" },
            { name: "Pinecone", desc: "Vector DB", url: "https://www.pinecone.io" },
            { name: "Cohere Rerank", desc: "Reranking", url: "https://cohere.com" },
            { name: "WebLLM", desc: "In-browser model inference", url: "https://webllm.mlc.ai" },
            { name: "RAGAS", desc: "Evaluation Harness", url: "https://docs.ragas.io" }
          ]},
          { label: "Infra", items: [
            { name: "Netlify", desc: "Hosting", url: "https://www.netlify.com" },
            { name: "Cloudflare Turnstile", desc: "Request verification", url: "https://developers.cloudflare.com/turnstile/" },
            { name: "Cloudflare Workers", desc: "API/Backend", url: "https://workers.cloudflare.com/" },
            { name: "Cloudflare D1 & KV", desc: "Question logs & answer cache", url: "https://developers.cloudflare.com/d1/" },
            { name: "Next.js", desc: "React & TypeScript frontend", url: "https://nextjs.org" },
            { name: "Tailwind", desc: "CSS", url: "https://tailwindcss.com" },
            { name: "Docker", desc: "Ingestion Pipeline", url: "https://www.docker.com" }
          ]},
        ].map((section) => (
          <div key={section.label} className="mb-2">
            <span className="text-[10px] font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mr-2">{section.label}:</span>
            {section.items.map((tech, i) => (
              <React.Fragment key={tech.name}>
                <span className="relative inline-block group">
                  <a href={tech.url} target="_blank" rel="noopener noreferrer" className="text-xs text-gray-600 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400">
                    {tech.name}
                  </a>
                  <span className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-2 py-1 text-[10px] text-white bg-gray-800 dark:bg-gray-700 rounded whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity duration-100">
                    {tech.desc}
                  </span>
                </span>
                {i < section.items.length - 1 && <span className="text-gray-300 dark:text-gray-600 mx-1">·</span>}
              </React.Fragment>
            ))}
          </div>
        ))}
      </div>

      <footer className="mt-12 pt-6 border-t border-gray-200 dark:border-gray-800 text-center text-xs text-gray-500 dark:text-gray-400">
        Built by <a href="https://www.linkedin.com/in/anant-j" className="underline hover:text-blue-400" target="_blank" rel="noopener noreferrer">Anant Jain</a> · AI Engineer · Canadian Pilot (PPL) · {" "}
        <a href="https://github.com/anant-j/AeroQuery" className="underline hover:text-blue-400" target="_blank" rel="noopener noreferrer">GitHub</a>
      </footer>
    </main>
  );
}
