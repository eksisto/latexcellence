import { Link } from "react-router-dom";

export default function WelcomePage() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-8">
      <h1 className="text-4xl font-bold mb-2">LateXcellence</h1>
      <p className="text-gray-500 mb-8 text-lg">
        AI-powered LaTeX document assistant
      </p>
      <div className="grid grid-cols-2 gap-4 max-w-lg w-full">
        <Link
          to="/workspace"
          className="p-6 rounded-xl bg-white shadow-md hover:shadow-lg transition-shadow border border-gray-100"
        >
          <div className="text-xl font-semibold mb-1">Project Workspace</div>
          <div className="text-sm text-gray-500">Open and edit LaTeX projects</div>
        </Link>
        <Link
          to="/formula-ocr"
          className="p-6 rounded-xl bg-white shadow-md hover:shadow-lg transition-shadow border border-gray-100"
        >
          <div className="text-xl font-semibold mb-1">Formula OCR</div>
          <div className="text-sm text-gray-500">Convert images to LaTeX formulas</div>
        </Link>
        <Link
          to="/template-gen"
          className="p-6 rounded-xl bg-white shadow-md hover:shadow-lg transition-shadow border border-gray-100"
        >
          <div className="text-xl font-semibold mb-1">Template Gen</div>
          <div className="text-sm text-gray-500">Generate LaTeX templates from descriptions</div>
        </Link>
        <Link
          to="/typo-check"
          className="p-6 rounded-xl bg-white shadow-md hover:shadow-lg transition-shadow border border-gray-100"
        >
          <div className="text-xl font-semibold mb-1">Typo Check</div>
          <div className="text-sm text-gray-500">Find and fix typos in your document</div>
        </Link>
        <Link
          to="/expression-opt"
          className="p-6 rounded-xl bg-white shadow-md hover:shadow-lg transition-shadow border border-gray-100"
        >
          <div className="text-xl font-semibold mb-1">Expression Optimize</div>
          <div className="text-sm text-gray-500">Improve academic writing style</div>
        </Link>
      </div>
      <div className="mt-8 text-sm text-gray-400">v0.1.0</div>
    </div>
  );
}
