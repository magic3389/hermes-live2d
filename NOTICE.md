# Notice — 授权范围与第三方组件

本仓库**代码与文档**采用 [MIT](LICENSE)。

## 不在 MIT 范围内的内容

| 内容 | 说明 |
|---|---|
| `assets/` 下的示例立绘与渲染图 | 仅作效果演示；形象版权归作者，**不得再分发或商用** |
| 任何模型权重 | 不随仓库分发，见下表 |

## 上游组件（均需自行下载）

| 组件 | 用途 | 许可 |
|---|---|---|
| [See-through](https://github.com/shitagaki-lab/see-through) | 单张立绘 → 语义分层 PSD | 见上游仓库 |
| [Iki](https://github.com/ikijs/iki) | `.iki` 格式、编辑器、网页播放器 | MIT |
| [RMBG-1.4](https://huggingface.co/briaai/RMBG-1.4) | 人物抠图 | **仅非商业** |
| [ComfyUI](https://github.com/comfyanonymous/ComfyUI) | 抠图节点宿主 | GPL-3.0 |
| [ComfyUI_LayerStyle](https://github.com/chflame163/ComfyUI_LayerStyle) | 提供 `LayerMask: RemBgUltra` | MIT |

## 商用提醒

RMBG-1.4 的许可是**非商业**。若要商用，替换
`pipeline/matting/rmbg_cut.py` 里的抠图节点为同等能力的可商用模型即可，
该脚本的输入输出契约不变（进：ComfyUI input 下的图；出：mask 灰度图）。

## 本仓库不包含

- 任何立绘素材、拆分后的 PSD、`.iki` 模型
- See-through / RMBG 的权重文件
- 任何个人路径、密钥、API token
