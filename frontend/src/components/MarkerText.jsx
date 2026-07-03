import React from 'react';

const MARKER_LABELS = {
  MATH: "公式",
  CMD: "命令",
  ENV: "环境",
  VERB: "代码"
};

const MARKER_COLORS = {
  MATH: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200 border-blue-200 dark:border-blue-800",
  CMD: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200 border-purple-200 dark:border-purple-800",
  ENV: "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200 border-orange-200 dark:border-orange-800",
  VERB: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200 border-gray-200 dark:border-gray-700"
};

export default function MarkerText({ text }) {
  if (!text) return null;
  
  const tokens = text.split(/(__(?:MATH|CMD|ENV|VERB)_\d{4}__)/g);
  
  return (
    <>
      {tokens.map((token, index) => {
        const m = token.match(/^__(MATH|CMD|ENV|VERB)_(\d{4})__$/);
        if (m) {
          const type = m[1];
          const num = m[2];
          return (
            <span 
              key={index} 
              className={`inline-flex items-center px-1 py-0.5 rounded text-[10px] font-medium mx-0.5 border ${MARKER_COLORS[type]} align-middle shadow-sm`}
              title={token}
            >
              {MARKER_LABELS[type]}-{num}
            </span>
          );
        }
        return <React.Fragment key={index}>{token}</React.Fragment>;
      })}
    </>
  );
}