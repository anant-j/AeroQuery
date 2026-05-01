"use client";

import React, { useState, useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import { CreateMLCEngine, type MLCEngine } from "@mlc-ai/web-llm";
import { RAG_SYSTEM_PROMPT, BARE_SYSTEM_PROMPT } from "./lib/prompts";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:7071/api";
const WEBLLM_MODEL = "Llama-3.2-1B-Instruct-q4f16_1-MLC";

const EVAL_DATA = [
  {
    model: "GPT-5.4-mini",
    metrics: {
      factual_correctness: { bare: 0.32, rag: 0.40, rerank: 0.43 },
      faithfulness: { rag: 0.82, rerank: 0.88 },
      context_precision: { rag: 0.78, rerank: 0.92 },
      context_recall: { rag: 0.88, rerank: 0.92 },
      answer_relevancy: { bare: 0.84, rag: 0.78, rerank: 0.79 },
      semantic_similarity: { bare: 0.69, rag: 0.77, rerank: 0.77 },
    },
  },
  {
    model: "GPT-3.5-turbo",
    metrics: {
      factual_correctness: { bare: 0.23, rag: 0.44, rerank: 0.44 },
      faithfulness: { rag: 0.82, rerank: 0.85 },
      context_precision: { rag: 0.78, rerank: 0.92 },
      context_recall: { rag: 0.93, rerank: 0.93 },
      answer_relevancy: { bare: 0.83, rag: 0.86, rerank: 0.87 },
      semantic_similarity: { bare: 0.74, rag: 0.80, rerank: 0.79 },
    },
  },
  {
    model: "Llama 3.2 1B (WebLLM)",
    metrics: {
      factual_correctness: { bare: 0.07, rag: 0.18, rerank: 0.22 },
      faithfulness: { rag: 0.52, rerank: 0.55 },
      context_precision: { rag: 0.78, rerank: 0.93 },
      context_recall: { rag: 0.94, rerank: 0.90 },
      answer_relevancy: { bare: 0.79, rag: 0.58, rerank: 0.62 },
      semantic_similarity: { bare: 0.66, rag: 0.67, rerank: 0.69 },
    },
  },
];

const METRIC_LABELS: Record<string, string> = {
  factual_correctness: "Factual Correctness",
  faithfulness: "Faithfulness",
  context_precision: "Context Precision",
  context_recall: "Context Recall",
  answer_relevancy: "Answer Relevancy",
  semantic_similarity: "Semantic Similarity",
};

const METRIC_INSIGHTS: Record<string, string> = {
  factual_correctness: "RAG triples Llama accuracy. Reranking adds another boost.",
  faithfulness: "GPT models stay grounded. Llama often ignores context.",
  context_precision: "Cohere rerank: 0.78 → 0.92. Right chunks, right order.",
  context_recall: "94% recall — retrieval finds the right information.",
  answer_relevancy: "Bare LLM scores higher by confidently hallucinating relevant-sounding answers. RAG correctly refuses when unsure — for aviation safety, faithfulness > relevancy.",
  semantic_similarity: "RAG answers are consistently closer to ground truth.",
};

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

export default function Home() {
  const defaultQuery = "What is the minimum amount of time I need to wait after drinking alcohol before flying VFR?";
  const [query, setQuery] = useState("");
  const [modelOption, setModelOption] = useState<ModelOption>("openai");
  const [loading, setLoading] = useState(false);
  const [ragResult, setRagResult] = useState<AskResponse | null>(null);
  const [bareResult, setBareResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState("");
  const [ragStreaming, setRagStreaming] = useState(false);
  const [bareStreaming, setBareStreaming] = useState(false);

  // WebLLM state
  const [webllmReady, setWebllmReady] = useState(false);
  const [webllmProgress, setWebllmProgress] = useState(0);
  const [webllmStatus, setWebllmStatus] = useState("Initializing...");
  const [webllmLoading, setWebllmLoading] = useState(true);
  const engineRef = useRef<MLCEngine | null>(null);

  // Load WebLLM model on page load
  useEffect(() => {
    let cancelled = false;

    async function loadWebLLM() {
      try {
        const engine = await CreateMLCEngine(WEBLLM_MODEL, {
          initProgressCallback: (progress) => {
            if (cancelled) return;
            setWebllmProgress(Math.round(progress.progress * 100));
            setWebllmStatus(progress.text);
          },
        });
        if (!cancelled) {
          engineRef.current = engine;
          setWebllmReady(true);
          setWebllmLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setWebllmStatus("WebGPU not supported in this browser");
          setWebllmLoading(false);
        }
      }
    }

    loadWebLLM();
    return () => { cancelled = true; };
  }, []);

  async function readSSEStream(
    body: { query: string; use_rag: boolean },
    onToken: (token: string) => void,
    onSources?: (sources: Source[], agent?: AskResponse["agent"]) => void,
    onMeta?: (meta: { tokens?: number; model?: string }) => void,
  ) {
    const res = await fetch("/api/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!res.ok) throw new Error("Stream request failed");
    if (!res.body) throw new Error("No response body");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === "token") onToken(data.token);
            else if (data.type === "sources" && onSources) onSources(data.sources, data.agent);
            else if (data.type === "usage" && onMeta) onMeta({ tokens: data.tokens, model: data.model });
          } catch {
            // Skip malformed SSE events
          }
        }
      }
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const effectiveQuery = query.trim() || defaultQuery;

    setLoading(true);
    setError("");
    setRagResult(null);
    setBareResult(null);
    setRagStreaming(false);
    setBareStreaming(false);

    try {
      if (modelOption === "openai") {
        // Initialize results with empty answers for streaming
        setRagResult({ query: effectiveQuery, answer: "", model: "", tokens: 0, use_rag: true, sources: [] });
        setBareResult({ query: effectiveQuery, answer: "", model: "", tokens: 0, use_rag: false });
        setRagStreaming(true);
        setBareStreaming(true);

        // Stream both RAG and bare LLM in parallel
        await Promise.all([
          readSSEStream(
            { query: effectiveQuery, use_rag: true },
            (token) => setRagResult(prev => prev ? { ...prev, answer: prev.answer + token } : null),
            (sources, agent) => setRagResult(prev => prev ? { ...prev, sources, agent } : null),
            (meta) => setRagResult(prev => prev ? { ...prev, ...meta } : null),
          ).finally(() => setRagStreaming(false)),
          readSSEStream(
            { query: effectiveQuery, use_rag: false },
            (token) => setBareResult(prev => prev ? { ...prev, answer: prev.answer + token } : null),
            undefined,
            (meta) => setBareResult(prev => prev ? { ...prev, ...meta } : null),
          ).finally(() => setBareStreaming(false)),
        ]);
      } else {
        // WebLLM: retrieve chunks from server, generate locally with streaming
        const retrieveRes = await fetch(`${API_URL}/retrieve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: effectiveQuery }),
        });

        if (!retrieveRes.ok) throw new Error("Retrieval API error");
        const retrieveData = await retrieveRes.json();
        const chunks: Chunk[] = retrieveData.chunks;

        const context = chunks.map((c) =>
          `[Section ${c.section}]\n${c.text}`
        ).join("\n\n---\n\n");

        const ragMessages = [
          { role: "system" as const, content: RAG_SYSTEM_PROMPT },
          { role: "user" as const, content: `Context from Canadian Aviation Regulations:\n\n${context}\n\n---\n\nQuestion: ${effectiveQuery}` },
        ];
        const bareMessages = [
          { role: "system" as const, content: BARE_SYSTEM_PROMPT },
          { role: "user" as const, content: effectiveQuery },
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
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
      setRagStreaming(false);
      setBareStreaming(false);
    }
  };

  const SourceItem = ({ source }: { source: Source }) => {
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
  };

  const ResultCard = ({ result, label, accent, streaming }: { result: AskResponse; label: string; accent: string; streaming?: boolean }) => (
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

  // Theme toggle
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    setIsDark(document.documentElement.classList.contains("dark"));
  }, []);

  const toggleTheme = () => {
    const next = !isDark;
    setIsDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
  };

  return (
    <main className="w-full max-w-[1400px] mx-auto px-8 py-12">
      <div className="flex items-start justify-between mb-10">
        <div>
          <h1 className="text-3xl font-bold text-gray-800 dark:text-gray-100 mb-2">AeroQuery</h1>
          <p className="text-gray-500 dark:text-gray-400">
            Ask questions about Canadian aviation regulations. Powered by RAG over the TC AIM.
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

      <form onSubmit={handleSubmit} className="mb-8">
        <div className="flex gap-3 mb-3">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={defaultQuery}
            aria-label="Search Canadian aviation regulations"
            className="flex-1 border border-gray-200 dark:border-gray-700 rounded-lg px-4 py-3 text-sm text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-900 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            type="submit"
            disabled={loading || (modelOption === "webllm" && !webllmReady)}
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
            <option value="openai">OpenAI (GPT-5.4-mini)</option>
            <option value="webllm" disabled={webllmLoading}>
              {webllmLoading
                ? `WebLLM (Llama 3.2 1B) — ${webllmProgress}%`
                : webllmReady
                  ? "WebLLM (Llama 3.2 1B)"
                  : "WebLLM — unavailable"
              }
            </option>
          </select>
          {webllmLoading && (
            <span className="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1">
              <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24" aria-hidden="true">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              <span aria-live="polite">{webllmStatus.length > 50 ? webllmStatus.slice(0, 50) + "..." : webllmStatus}</span>
            </span>
          )}
          {webllmReady && modelOption === "webllm" && (
            <span className="text-xs text-green-500">Ready — runs in your browser</span>
          )}
        </div>

        <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
          Runs the same question with and without RAG so you can compare the answers side by side.
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
          50 questions · 6 metrics · 3 models · Judged by GPT-5.4-mini via Azure AI Foundry
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
                // Calculate best improvement across all models for this metric
                const allBare = EVAL_DATA.map(r => {
                  const m = r.metrics[metric as keyof typeof r.metrics];
                  return "bare" in m ? m.bare : null;
                }).filter(v => v !== null) as number[];
                const allRerank = EVAL_DATA.map(r => {
                  const m = r.metrics[metric as keyof typeof r.metrics];
                  return "rerank" in m ? m.rerank : null;
                }).filter(v => v !== null) as number[];

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
          <span className="text-emerald-600 dark:text-emerald-400">↑ Green</span> = best score (improvement). <span className="text-red-500 dark:text-red-400">↓ Red</span> = worst score (regression). Retrieval metrics are model-independent.
        </p>
      </div>

      <div className="border-t border-gray-100 dark:border-gray-800 pt-6 mt-8">
        <h2 className="text-sm font-semibold text-gray-500 dark:text-gray-400 mb-3">Built With</h2>
        {[
          { label: "AI & Retrieval", items: [
            { name: "OpenAI", desc: "Embeddings & LLM", url: "https://platform.openai.com" },
            { name: "Pinecone", desc: "Vector DB", url: "https://www.pinecone.io" },
            { name: "Cohere Rerank", desc: "Reranking", url: "https://cohere.com" },
            { name: "WebLLM", desc: "In-browser LLM", url: "https://webllm.mlc.ai" },
            { name: "Ollama", desc: "Local inference", url: "https://ollama.com" },
          ]},
          { label: "Agentic", items: [
            { name: "LangGraph", desc: "Agent state machine", url: "https://langchain-ai.github.io/langgraph/" },
            { name: "LangChain", desc: "LLM orchestration", url: "https://www.langchain.com" },
          ]},
          { label: "Eval & Ops", items: [
            { name: "RAGAS", desc: "6 metrics", url: "https://docs.ragas.io" },
            { name: "LiteLLM", desc: "Multi-provider", url: "https://docs.litellm.ai" },
            { name: "Azure AI Foundry", desc: "Judge model", url: "https://ai.azure.com" },
          ]},
          { label: "Infra", items: [
            { name: "Azure Functions", desc: "API", url: "https://azure.microsoft.com/en-us/products/functions" },
            { name: "Next.js", desc: "Frontend", url: "https://nextjs.org" },
            { name: "Tailwind", desc: "CSS", url: "https://tailwindcss.com" },
            { name: "Netlify", desc: "Hosting", url: "https://www.netlify.com" },
            { name: "Docker", desc: "Pipeline", url: "https://www.docker.com" },
            { name: "PyMuPDF", desc: "PDF parsing", url: "https://pymupdf.readthedocs.io" },
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
        Built by Anant Jain · AI Engineer · Canadian pilot ·{" "}
        <a href="https://github.com/anant-j/AeroQuery" className="underline hover:text-blue-400" target="_blank" rel="noopener noreferrer">GitHub</a>
      </footer>
    </main>
  );
}
