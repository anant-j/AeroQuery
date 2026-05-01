"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:7071/api";

const EVAL_DATA = [
  { config: "GPT-5.4-mini + RAG + Rerank", correctness: 0.92, faithfulness: 0.95 },
  { config: "GPT-5.4-mini + RAG (no rerank)", correctness: 0.91, faithfulness: 0.98 },
  { config: "GPT-5.4-mini Bare", correctness: 0.81, faithfulness: null },
  { config: "GPT-3.5 + RAG (no rerank)", correctness: 0.84, faithfulness: 0.87 },
  { config: "GPT-3.5 Bare", correctness: 0.68, faithfulness: null },
];

interface Source {
  section: string;
  title: string;
}

interface AskResponse {
  query: string;
  answer: string;
  model: string;
  tokens: number;
  use_rag: boolean;
  sources?: Source[];
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [ragResult, setRagResult] = useState<AskResponse | null>(null);
  const [bareResult, setBareResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;

    setLoading(true);
    setError("");
    setRagResult(null);
    setBareResult(null);

    try {
      const res = await fetch(`${API_URL}/compare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });

      if (!res.ok) throw new Error("API error");

      const data = await res.json();
      setRagResult({
        query: data.query,
        answer: data.rag.answer,
        model: data.model,
        tokens: data.rag.tokens,
        use_rag: true,
        sources: data.rag.sources,
      });
      setBareResult({
        query: data.query,
        answer: data.bare.answer,
        model: data.model,
        tokens: data.bare.tokens,
        use_rag: false,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  const ResultCard = ({ result, label, accent }: { result: AskResponse; label: string; accent: string }) => (
    <div className={`border rounded-lg p-5 ${accent}`}>
      <div className="flex items-center gap-2 mb-3">
        <span className={`text-xs px-2 py-1 rounded font-medium ${label === "RAG" ? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300" : "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300"}`}>
          {label}
        </span>
        <span className="text-xs text-gray-400 dark:text-gray-500">
          {result.tokens} tokens
        </span>
      </div>

      <div className="prose prose-sm prose-gray dark:prose-invert max-w-none mb-3">
        <ReactMarkdown>{result.answer}</ReactMarkdown>
      </div>

      {result.sources && result.sources.length > 0 && (
        <div className="border-t border-gray-100 dark:border-gray-700 pt-3 mt-3">
          <p className="text-xs font-medium text-gray-400 dark:text-gray-500 mb-2">Sources</p>
          <div className="flex flex-wrap gap-1.5">
            {result.sources.map((s, i) => (
              <span key={i} className="text-xs bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 px-2 py-0.5 rounded">
                §{s.section}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );

  return (
    <main className="max-w-5xl mx-auto px-4 py-12">
      <div className="mb-10">
        <h1 className="text-3xl font-bold text-gray-800 dark:text-gray-100 mb-2">AeroQuery</h1>
        <p className="text-gray-400 dark:text-gray-500">
          Ask questions about Canadian aviation regulations. Powered by RAG over the TC AIM.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="mb-8">
        <div className="flex gap-3">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. What are the fuel requirements for VFR flight?"
            className="flex-1 border border-gray-200 dark:border-gray-700 rounded-lg px-4 py-3 text-sm text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-900 placeholder-gray-300 dark:placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="bg-blue-600 text-white px-6 py-3 rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "Searching..." : "Compare"}
          </button>
        </div>
        <p className="text-xs text-gray-300 dark:text-gray-600 mt-2">
          Runs the same question with and without RAG so you can compare the answers side by side.
        </p>
      </form>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-600 px-4 py-3 rounded-lg mb-6 text-sm">
          {error}
        </div>
      )}

      {(ragResult || bareResult) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-10">
          {ragResult && (
            <ResultCard result={ragResult} label="RAG" accent="border-green-200 dark:border-green-800 bg-green-50/30 dark:bg-green-950/30" />
          )}
          {bareResult && (
            <ResultCard result={bareResult} label="Bare LLM" accent="border-yellow-200 dark:border-yellow-800 bg-yellow-50/30 dark:bg-yellow-950/30" />
          )}
        </div>
      )}

      <div className="border-t border-gray-100 dark:border-gray-800 pt-8">
        <h2 className="text-lg font-semibold text-gray-700 dark:text-gray-200 mb-4">Eval Benchmark</h2>
        <p className="text-sm text-gray-400 dark:text-gray-500 mb-4">
          25 questions, judged by GPT-5.4-mini. Reranking via Cohere rerank-v4.0-pro.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 dark:border-gray-700">
                <th className="text-left py-2 pr-4 font-medium text-gray-500 dark:text-gray-400">Configuration</th>
                <th className="text-right py-2 px-4 font-medium text-gray-500 dark:text-gray-400">Correctness</th>
                <th className="text-right py-2 pl-4 font-medium text-gray-500 dark:text-gray-400">Faithfulness</th>
              </tr>
            </thead>
            <tbody>
              {EVAL_DATA.map((row, i) => (
                <tr key={i} className="border-b border-gray-50 dark:border-gray-800">
                  <td className="py-2 pr-4 text-gray-600 dark:text-gray-300">{row.config}</td>
                  <td className="text-right py-2 px-4 font-mono text-gray-600 dark:text-gray-300">{row.correctness.toFixed(2)}</td>
                  <td className="text-right py-2 pl-4 font-mono text-gray-600 dark:text-gray-300">
                    {row.faithfulness !== null ? row.faithfulness.toFixed(2) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <footer className="mt-12 pt-6 border-t border-gray-50 dark:border-gray-800 text-center text-xs text-gray-300 dark:text-gray-600">
        Built by Anant Jain · AI Engineer · Canadian pilot ·{" "}
        <a href="https://github.com/anant-j" className="underline hover:text-gray-500" target="_blank" rel="noopener noreferrer">GitHub</a>
      </footer>
    </main>
  );
}
