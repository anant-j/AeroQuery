"use client";

import { useState } from "react";

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
  const [useRag, setUseRag] = useState(true);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;

    setLoading(true);
    setError("");
    setResult(null);

    try {
      const res = await fetch(`${API_URL}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, use_rag: useRag }),
      });

      if (!res.ok) throw new Error(`API error: ${res.status}`);

      const data: AskResponse = await res.json();
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="max-w-3xl mx-auto px-4 py-12">
      <div className="mb-10">
        <h1 className="text-3xl font-bold mb-2">AeroQuery</h1>
        <p className="text-gray-500">
          Ask questions about Canadian aviation regulations. Powered by RAG over the TC AIM.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="mb-8">
        <div className="flex gap-3 mb-3">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. What are the fuel requirements for VFR flight?"
            className="flex-1 border border-gray-300 rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="bg-blue-600 text-white px-6 py-3 rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "Searching..." : "Ask"}
          </button>
        </div>

        <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer">
          <input
            type="checkbox"
            checked={useRag}
            onChange={(e) => setUseRag(e.target.checked)}
            className="rounded"
          />
          Use RAG (retrieval-augmented generation)
          <span className="text-gray-400">
            {useRag ? "— answers grounded in TC AIM" : "— bare LLM knowledge only"}
          </span>
        </label>
      </form>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-6 text-sm">
          {error}
        </div>
      )}

      {result && (
        <div className="border border-gray-200 rounded-lg p-6 mb-10">
          <div className="flex items-center gap-2 mb-4">
            <span className={`text-xs px-2 py-1 rounded font-medium ${result.use_rag ? "bg-green-100 text-green-700" : "bg-yellow-100 text-yellow-700"}`}>
              {result.use_rag ? "RAG" : "Bare LLM"}
            </span>
            <span className="text-xs text-gray-400">
              {result.model} · {result.tokens} tokens
            </span>
          </div>

          <div className="prose prose-sm max-w-none mb-4 whitespace-pre-wrap">
            {result.answer}
          </div>

          {result.sources && result.sources.length > 0 && (
            <div className="border-t border-gray-100 pt-3 mt-4">
              <p className="text-xs font-medium text-gray-500 mb-2">Sources</p>
              <div className="flex flex-wrap gap-2">
                {result.sources.map((s, i) => (
                  <span key={i} className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded">
                    §{s.section}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="border-t border-gray-200 pt-8">
        <h2 className="text-lg font-semibold mb-4">Eval Benchmark</h2>
        <p className="text-sm text-gray-500 mb-4">
          25 questions, judged by GPT-5.4-mini. Reranking via Cohere rerank-v4.0-pro.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-left py-2 pr-4 font-medium text-gray-600">Configuration</th>
                <th className="text-right py-2 px-4 font-medium text-gray-600">Correctness</th>
                <th className="text-right py-2 pl-4 font-medium text-gray-600">Faithfulness</th>
              </tr>
            </thead>
            <tbody>
              {EVAL_DATA.map((row, i) => (
                <tr key={i} className="border-b border-gray-100">
                  <td className="py-2 pr-4 text-gray-800">{row.config}</td>
                  <td className="text-right py-2 px-4 font-mono">{row.correctness.toFixed(2)}</td>
                  <td className="text-right py-2 pl-4 font-mono">
                    {row.faithfulness !== null ? row.faithfulness.toFixed(2) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <footer className="mt-12 pt-6 border-t border-gray-100 text-center text-xs text-gray-400">
        Built by Anant Jain · Canadian student pilot ·{" "}
        <a href="https://github.com/anantjain" className="underline hover:text-gray-600" target="_blank" rel="noopener noreferrer">GitHub</a>
      </footer>
    </main>
  );
}
