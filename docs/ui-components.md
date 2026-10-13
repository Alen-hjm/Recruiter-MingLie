# 界面组件来源与维护

明猎直接改造了以下 React Bits 组件源码，保留其核心 CSS 和指针坐标计算，移除 React 包装。它们随招聘产品分发，不作为独立组件库出售或分发。

固定上游版本：[`c83b5e7e026f003af33ff9ebc3d93afb5c86ad22`](https://github.com/DavidHDev/react-bits/tree/c83b5e7e026f003af33ff9ebc3d93afb5c86ad22)。选用该版本简洁的 CSS 实现，不引入新版的 Canvas 渲染循环。

| 组件源码 | 使用位置 | 明猎的修改 |
| --- | --- | --- |
| [SpotlightCard](https://github.com/DavidHDev/react-bits/tree/c83b5e7e026f003af33ff9ebc3d93afb5c86ad22/src/content/Components/SpotlightCard) | 助手首屏、候选人、最近招聘项目，含浏览器侧栏 | 白底紫灰聚光，缩小影响范围；键盘聚焦支持；指针事件按帧合并；动态结果自动挂载 |
| [StarBorder](https://github.com/DavidHDev/react-bits/tree/c83b5e7e026f003af33ff9ebc3d93afb5c86ad22/src/content/Animations/StarBorder) | 工作台与扩展的聊天输入框 | 保留双渐变与相向移动关键帧；蒙版只露出细边；仅聚焦时播放；保留输入框布局与原生焦点边界 |

## 授权

上游授权为 **MIT + Commons Clause**，不是不附带限制的 MIT。允许作为应用、网站或产品的一部分使用（包括商业用途），限制对组件本身单独或打包销售、再授权或再分发。原版权和授权全文保存在 [`chrome-extension/THIRD_PARTY_NOTICES.md`](../chrome-extension/THIRD_PARTY_NOTICES.md)，随插件目录一同交付。

本轮没有复制 MotionSites 的网站源码、Uiverse 未署名代码或 Aceternity 付费组件，也没有引入 Anime.js 运行时。请勿把设计参考标为源码依赖。

## 维护

源文件：`static/js/ui-components.js`、`static/css/ui-components.css`。

扩展需要脱离服务端独立加载，因此携带相同文件的本地副本。修改源文件后执行：

```bash
python scripts/sync_ui_components.py
python scripts/sync_ui_components.py --check
python scripts/verify_extension.py
```

两个端都支持 `prefers-reduced-motion`，离屏或页面隐藏时暂停动画；无远程脚本、无新运行时依赖。装饰节点不接收指针事件，不改变点击、输入、导航或真实招聘任务状态。
