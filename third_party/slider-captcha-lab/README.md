# slider-captcha-lab 来源说明

- 上游项目：https://github.com/kangleyao/slider-captcha-lab
- 参考版本：[3424e33a045cc66a5b6725aea43da41aca047930](https://github.com/kangleyao/slider-captcha-lab/tree/3424e33a045cc66a5b6725aea43da41aca047930)
- 作者与许可：Copyright (c) 2026 slider-captcha-lab contributors；MIT，完整原文见 [LICENSE](LICENSE)。

本项目 `slider.js` 中的 `generateDrag` 将上游 `generate_drag` 的 `baseline_current` 和 `feedback` 轨迹生成逻辑改编为 JavaScript。保留了速度积分、非均匀采样及反馈轨迹中的分段运动、量化、停顿和局部修正。

针对条形滑块进行了以下调整：移除拼图专用的终点越界和回拉；限制横向位置在滑轨内并保持终点距离；限制轨迹时长；按可见视口缩放纵向偏移。随机数可注入，以便测试边界和取消行为。

官网 DOM 识别、浏览器调试连接、SN 队列和结果记录由本项目实现。此处保留上游许可，不引入其独立服务或远程依赖。

## Python 集成

Python 版现在直接调用用户下载的 `slider-captcha-lab-main/human_track.py` 中的 `generate_drag(..., variant="feedback")`。原文件完整复制到 [`dji_checker/_vendor/slider_captcha_lab/human_track.py`](../../dji_checker/_vendor/slider_captcha_lab/human_track.py)，没有修改上游代码，旁边保留了该下载版本的原始 LICENSE。

- 本地源文件 SHA-256：`9015631f14502481690b6150f21f10dc30cada77d0dba058f50fb0150449c7fc`。
- 此 Python 副本来自本地下载目录；不把它假定为上面扩展版所参考的 Git 提交。
- 运行时使用固定副本，移动或删除下载目录不会影响程序；只新增 NumPy 依赖，不加载 YOLO、训练脚本或缺口识别模型。

[`dji_checker/tracks.py`](../../dji_checker/tracks.py) 负责条形滑块适配：把 `(dx, dy, dt_ms)` 增量转换成绝对坐标和秒时间戳，保留分段变速、停顿、非均匀采样及中段小幅修正；删除最远点之后的末端回拉，并把最远点缩放到滑轨末端。纵向偏移按比例限制到 ±4 CSS 像素，动态时长在 0.9–3.1 秒内变化。诊断文件记录 `track_source`、`track_variant` 和本次时长，便于核实实际调用来源。

此前 Python 版的轨迹是独立实现的简化变速曲线，没有调用此上游核心。上游 README 的通过率来自其自身环境与样本，不能代表大疆官网通过率；本项目的本地测试只验证输入、重试与队列行为。
