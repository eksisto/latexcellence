/**
 * ProjectExplorer 组件 —— 项目文件树
 *
 * 展开显示 LaTeX 项目的目录结构。
 * 用户点击文件可以选择为目标操作对象。
 *
 * props:
 *   @param {Array}    tree      - 文件树节点列表（FileNode 结构）
 *   @param {Function} onSelect  - 选中节点时的回调 (node) => void
 *
 * FileNode 结构：
 *   { name: string, path: string, is_dir: boolean, children: FileNode[] }
 *
 * 后续实现：
 *   - 目录可折叠/展开
 *   - 右键菜单（重命名、删除等）
 *   - 文件图标根据扩展名区分
 *   - 搜索/过滤文件
 *   - 拖拽排序
 */
export default function ProjectExplorer({ tree = [], onSelect }) {
  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white h-full">
      <h3 className="font-semibold text-sm text-gray-500 mb-3">
        Project Files
      </h3>
      {tree.length === 0 ? (
        <p className="text-gray-300 text-sm">No project opened</p>
      ) : (
        <ul className="space-y-1">
          {tree.map((node) => (
            <li key={node.path} className="text-sm">
              <button
                className="text-left hover:text-blue-600 transition-colors"
                onClick={() => onSelect?.(node)}
              >
                {node.is_dir ? "📁" : "📄"} {node.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
