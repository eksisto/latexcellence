/**
 * GridSizePicker —— m×n 网格选择器（editor-plan §6.4 / 决策 Q9）。
 *
 * 面板中带 grid 配置的条目（tabular/matrix 家族/cases 等）进入二级步骤：
 * 仿 Word「插入表格」的拖选网格——按下并划过单元格高亮 m×n 矩形，松开
 * 即确认；也支持单击直接选定与方向键微调。行列上界取数据库校验常量
 * MAX_GRID_ROWS/COLS。
 *
 * 注：计划形态为 Popover 弹层，仓库 ui 基座未含 popover 原语，沿用阶段二
 * EditorSettingsDialog 的先例以 Dialog 承载，交互语义等价。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { MAX_GRID_COLS, MAX_GRID_ROWS } from "@/data/latexCommands/index.js";
import ArrowLeft from "lucide-react/dist/esm/icons/arrow-left.mjs";

/**
 * @param {object} props
 * @param {{name:string, zh:string, insert:string, grid:{rows:number,cols:number}}} props.entry
 *   带网格配置的命令条目
 * @param {(size: {rows:number, cols:number}) => void} props.onConfirm 选定行列
 * @param {() => void} [props.onBack] 返回命令列表
 */
export default function GridSizePicker({ entry, onConfirm, onBack }) {
  const { t } = useTranslation("components");
  const [size, setSize] = useState({
    rows: entry.grid?.rows ?? 1,
    cols: entry.grid?.cols ?? 1,
  });
  const draggingRef = useRef(false);

  /** 拖选结束统一在 window mouseup 提交（含拖出网格后松手） */
  useEffect(() => {
    const finish = () => {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      onConfirmRef.current(sizeRef.current);
    };
    window.addEventListener("mouseup", finish);
    return () => window.removeEventListener("mouseup", finish);
  }, []);

  const onConfirmRef = useRef(onConfirm);
  onConfirmRef.current = onConfirm;
  const sizeRef = useRef(size);
  sizeRef.current = size;

  const hoverCell = useCallback((rows, cols) => {
    setSize((prev) =>
      prev.rows === rows && prev.cols === cols ? prev : { rows, cols },
    );
  }, []);

  const handleCellPointerDown = (rows, cols) => (event) => {
    event.preventDefault();
    draggingRef.current = true;
    hoverCell(rows, cols);
  };

  const handleCellMouseEnter = (rows, cols) => () => {
    if (draggingRef.current) hoverCell(rows, cols);
  };

  const handleKeyDown = (event) => {
    const step = {
      ArrowUp: [-1, 0],
      ArrowDown: [1, 0],
      ArrowLeft: [0, -1],
      ArrowRight: [0, 1],
    }[event.key];
    if (!step) return;
    event.preventDefault();
    setSize((prev) => ({
      rows: Math.min(Math.max(1, prev.rows + step[0]), MAX_GRID_ROWS),
      cols: Math.min(Math.max(1, prev.cols + step[1]), MAX_GRID_COLS),
    }));
  };

  const cells = [];
  for (let r = 1; r <= MAX_GRID_ROWS; r += 1) {
    for (let c = 1; c <= MAX_GRID_COLS; c += 1) {
      const active = r <= size.rows && c <= size.cols;
      cells.push(
        <button
          key={`${r}-${c}`}
          type="button"
          aria-label={`${r} × ${c}`}
          onMouseDown={handleCellPointerDown(r, c)}
          onMouseEnter={handleCellMouseEnter(r, c)}
          onClick={(event) => {
            // 指针点击（detail >= 1）的确认已由 window mouseup 统一提交，
            // 这里只响应键盘激活（Enter/Space 产生的 click detail === 0），
            // 否则同一手势会触发两次 onConfirm 导致模板双重插入
            if (event.detail > 0) return;
            onConfirm({ rows: r, cols: c });
          }}
          className={
            active
              ? "border border-primary bg-primary/60"
              : "border border-border/60 bg-muted/40"
          }
        />,
      );
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">
        {t("gridSizePicker.hint")}
      </p>
      <div
        className="flex items-center justify-between gap-3"
        onKeyDown={handleKeyDown}
      >
        <div
          className="grid aspect-square w-56 shrink-0 gap-0.5"
          style={{
            gridTemplateColumns: `repeat(${MAX_GRID_COLS}, minmax(0, 1fr))`,
          }}
          role="grid"
          aria-label={t("gridSizePicker.title")}
        >
          {cells}
        </div>
        <div className="flex flex-col items-center gap-2 text-sm">
          <span className="font-mono text-lg">
            {size.rows} × {size.cols}
          </span>
          <span className="text-muted-foreground">
            {t("gridSizePicker.sizeLabel", {
              rows: size.rows,
              cols: size.cols,
            })}
          </span>
        </div>
      </div>
      <div className="flex justify-between">
        {onBack ? (
          <Button variant="ghost" size="sm" onClick={onBack}>
            <ArrowLeft />
            {t("gridSizePicker.back")}
          </Button>
        ) : (
          <span />
        )}
        <Button
          size="sm"
          onClick={() => onConfirm({ rows: size.rows, cols: size.cols })}
        >
          {t("gridSizePicker.confirm")}
        </Button>
      </div>
    </div>
  );
}
