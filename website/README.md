# Meítǐ Shāngchéng 员工管理网站

当前网站版本：`2.1.0-flask`；展示日期：2026年10月28日。第2组：梁邦羽、李远向、柳懿轩、刘帅。老师为甲方，团队为乙方。

本轮E01–E10是用户2026年10月26日授权的增强迭代，不是教师新增要求或批准。已通过的FR-01–FR-12保留。原2.0最终包另存`Meiti_Group2_Final_v2.0.0_20261028.zip`，原字节与SHA保留；文档/PPT由各负责人同步前，不重新冻结全套整包。10月28日展示具体时刻仍未确认，内部技术检查与老师决定分开记录。

本版本使用 **Python + Flask + SQLite + Jinja + CSS**。页面为英语，代码注释和本安装说明为中文。业务页面没有 JavaScript，也不依赖 Node.js、npm、浏览器脚本或外部数据库。原项目 `app/` 中的 Node 旧版保留，不是本次课堂运行入口。

## 1. 新电脑安装及启动

离线依赖包包含 `MarkupSafe-3.0.4-cp312-cp312-win_amd64.whl`，因此这一套离线交付只支持 **Windows x64 + CPython 3.12 64位**。不能直接用 Python 3.13/3.14、32位 Python、Windows ARM64、macOS 或 Linux 安装这组 wheels。

1. 从 [python.org](https://www.python.org/downloads/windows/) 安装 Windows CPython **3.12 x64**，保留 pip、venv；建议同时安装 Python Launcher。离线前需先准备好 Python 安装程序，当前包不包含 Python 安装程序。
2. 将网站和看板的完整运行包解压到可写目录，保留 `website/`、`scrum/` 的相对位置。
3. 双击 `website/Start.cmd`。脚本校验 Python 实际版本和64位架构，创建本机 `.venv`，只从随包 `wheelhouse/` 安装锁定的7个运行依赖，并执行 `pip check`。
4. 打开 **http://127.0.0.1:5000**，保持终端运行。停止时在对应终端按 Ctrl+C。再次启动保留已保存的数据。
5. 双击 `scrum/Start_Board.cmd` 启动独立看板，地址 **http://127.0.0.1:5001**。它复用网站环境，不要求网站已经运行。

如果电脑同时装有多个 Python，脚本优先使用已校验的现有环境，否则查找 `PYTHON312`、`py -3.12` 和 PATH 上符合条件的 Python。本交付电脑另有 Codex 附带的3.12运行时可回退使用；这不是可移植依赖。新电脑仍须自行安装 CPython 3.12 x64。

可在命令提示符中明确指定安装路径：

```bat
set "PYTHON312=C:\Python312\python.exe"
call "website\Start.cmd"
```

`.venv` 不是便携版 Python。请勿把本机 `.venv` 复制到另一台电脑当作运行依赖。遇到旧环境不兼容，保留业务 `instance/` 目录，将旧 `.venv` 改名移走后重新运行启动器；脚本不会擅自删除已有环境。

只安装环境而不启动服务器：双击 `Setup.cmd`。默认启动不需要联网；安装命令显式带 `--no-index --no-cache-dir --only-binary=:all:`，不会向在线包索引取包。完整依赖版本见 `requirements.txt`。

## 2. 演示账号

| 角色 | 用户名 | 初始密码 | 授权范围 |
| --- | --- | --- | --- |
| HR | `hr` | `Demo!2026` | 全店员工资料和搜索 |
| 主管 | `supervisor` | `Demo!2026` | Retail Operations，3名演示员工 |
| 第二主管 | `supervisor2` | `Demo!2026` | Customer Services，2名演示员工 |
| 员工 | `employee` | `Demo!2026` | Lin Wei 本人班表和签到 |
| 第二员工 | `employee2` | `Demo!2026` | Chen Rui 本人班表和签到 |

首次新库建立500条明确虚构的员工档案、5个账号、两支小团队、初始化当周和下一周的手工写定示例班表。姓名可重复。500条档案不代表500个账号或500人并发。后续启动不覆盖或自动补充演示数据，跨到其他周时可由主管手动排班。

## 3. 保存、删除和备份

网站数据库为 `website/instance/workforce.sqlite`，会话签名密钥为 `website/instance/secret.key`，首次运行在本机生成。运行包不携带工作数据库或密钥。

HR 删除会真正 `DELETE` 员工记录，外键级联删除关联 `users`、`sessions`、`assignments` 和 `checkins`；旧账号无法继续访问。`schedule_weeks` 的周容器可能保留，下一次完整保存仍按当前团队成员校验。删除页面要求输入准确姓名和当前版本。取消不会执行删除。

**独立备份不会因网站删除自动清除。** 删除前请理解这一区别；网站也不提供恢复已删除记录的业务页面。

安装管理员可备份到一个不存在的新文件：

```bat
.venv\Scripts\python.exe manage.py backup --output C:\MeitiBackups\workforce_20261028.sqlite
```

该命令使用 SQLite 备份接口。保管备份及密钥，备份含个人、薪资和签到信息。恢复前停止网站，保存当前数据的独立副本，再由安装管理员替换数据库并检查登录、记录和班表；不要在运行中直接覆盖文件。

## 4. 账号和团队是安装配置

HR 新增档案仅创建七组资料，不会自动创建登录账号或加入团队。部门字段不决定主管权限。安装管理员在本机配置账号和团队，不增加额外网站业务模块。

```bat
.venv\Scripts\python.exe manage.py accounts
.venv\Scripts\python.exe manage.py create-account --username staff.new --role employee --employee-id 501
.venv\Scripts\python.exe manage.py create-account --username lead.new --role supervisor --team-id 1
.venv\Scripts\python.exe manage.py create-account --username hr.new --role hr
.venv\Scripts\python.exe manage.py set-password --username employee
.venv\Scripts\python.exe manage.py assign-team --employee-id 501 --team-id 1
```

将示例数字替换成实际数据库记录标识；它们不是新增的业务编号字段。密码通过隐藏输入读取，至少8字符，不作为命令参数、不打印，保存为带盐哈希。员工账号只能绑定本人，主管账号只能绑定一支团队，HR账号不混带员工或团队范围。重置密码会撤销已有会话。已有排班的员工不能通过该简化命令直接转队；先由安装管理员评估、处理相关排班后再配置，避免跨团队遗留。

## 5. 签到边界与本地演示

默认 `ONSITE_NETWORKS=127.0.0.1/32,::1/128`，只有本机回环来源符合课堂模拟条件。网页明确标识为 **Classroom connection simulation**。本机成功只证明服务端来源规则和保存流程，不证明员工实际位于郑州门店。

来源读取实际 `request.remote_addr`，不信任客户端填报的位置或 `X-Forwarded-For`。远程个人班表查看与签到权限分开；不满足来源规则的提交被拒绝且不生成签到。重复发送同一个签名操作标识返回原结果，不重复新增。

`ONSITE_NETWORKS` 为空时全部签到拒绝；错误CIDR导致启动失败。`HOST`、`PORT` 可由环境变量指定，但本次只绑定 `127.0.0.1`，不部署公网。不提供已验证的门店/TLS远程拓扑；如将来实际部署，须另行验证门店独享来源、HTTPS、远程访问和拒绝规则，不能把共享代理/VPN地址当成物理在店证明。

## 6. 验证与使用说明

```bat
.venv\Scripts\python.exe tests\test_acceptance.py
```

自动化检查使用临时SQLite数据库，不修改课堂演示库。浏览器脚本在本机开发环境使用 Playwright，页面 JavaScript 关闭；Playwright 不在运行依赖及离线 wheels 中。不要为启动网站安装浏览器自动化工具。

实际用户步骤见 [用户手册.md](用户手册.md)，约3分钟的最短课堂流程及代码入口见 [演示与代码讲解.md](演示与代码讲解.md)。2.1交接以`coordination/website_handoff_2.1.md`为准。核心40项、看板11项、增强26项与本轮禁JS浏览器13项分开报告，以JSON执行日期和指纹为准；`browser_results.json`中的原8项及旧30项视觉记录属于2.0历史证据，不能冒充2.1本次执行。最终2.1页面见`website_enhancement_browser.json`。

本次“新建虚拟环境离线安装”是在当前Windows主机上验证，不等同于干净操作系统验收。尚未完成真实门店、外网/TLS、物理手机、独立同学GUI执行及老师最终验收。内部看板的拟定负责人也不代表个人贡献事实。

## 7. 2.1工作台与增强操作

登录先进入`/dashboard`，按服务端角色展示HR、主管或员工工作台。左侧导航保留旧业务页直达入口，390px改为可见顶部导航；帮助与版本是原生HTML入口。布局参考办公工作台，采用Meítǐ Shāngchéng自身品牌，无消息、审批等未实现模块和假角标。站点不从CDN加载资源。

HR目录支持实际部门/岗位选项、组合筛选、独立空字段条件和五个白名单排序。空部门与名字恰为“Unspecified”的真实部门分开，记录引用继续区分同名员工。详情、编辑、取消及返回保留筛选/排序/页码；不会接受任意`next`跳转。`Export all … matches · CSV`导出全部匹配，而非当前20行；界面列出八个字段和总数。文件为带BOM的UTF-8，csv标准库处理逗号/引号/换行，对潜在公式文本加单引号；仅HR可导出包含住址、薪资和紧急联系人的资料，请按实际保密要求保管。

主管的`/team-week`是只读周预览，`/team-schedule`手动编辑。逐员工显示工作日/班次数/计划小时；每天每班的数字是该班员工人数，People是当天去重人数，Assignments是当天班次数。每班固定4小时，汇总不代表实际出勤或工资。`Load previous week as draft`按星期只载入当前仍在团队的成员，新成员空白；不会更改目标周数据库。该链接会替换页面未保存选择，先阅读旁边提示。载入后需勾选已审阅草稿，再按`Save complete week`，仍执行整个团队的五天×两班校验和旧版本冲突检查。错误按成员展示，旧版本反复提交不会自动换用新版本。

员工`/my-schedule`提供上周/本周/下周、所选周汇总、独立今日安排和未来90天内的下一已保存班次。历史周不会改变真正“今日”；没有下一班时明示查询的起止时刻（UTC+8）。`/my-checkins`仅显示本人记录，from/to日期包含两端、20条分页，可清除；CSV导出全部匹配并排除operation_id。记录不等于出勤判定、签退、实际工时或工资。

三种打印友好入口分别为`/team-week/print`、`/my-schedule/print`、`/my-checkins/print`；打开后用浏览器打印菜单或Ctrl+P，无脚本打印按钮。主管限本团队，员工限本人；历史打印全部匹配。所有CSV、打印和页面路由都执行服务端鉴权，不可用参数切换身份或团队。

增强检查：`tests/test_enhancements.py`使用临时库；`tests/browser_enhancements.py`为独立开发工具，Playwright不加入七个运行依赖。增强结果和当前页面指纹见`evidence/website_enhancement_*.json`，跨文档同步契约在`coordination/website_enhancement_contract.md`。

## 8. 独立2.1运行包与日期

2.1网站与内部看板运行包为`evidence/website_runtime_bundle_2.1.0.zip`，源码清单为`website_runtime_manifest_2.1.0.json`，收据为`website_package_results_2.1.0.json`。它不包含尚待同步的课程Word/PPT，也不覆盖原2.0全套包或原`website_runtime_bundle.zip`。解压后保持`website`与`scrum`同级，按第1节启动。

2026年10月27日与28日属于周一2026年10月26日开始的同一周。工作台使用真实郑州日期，重新安装时样例仍只初始化当周和下一周，之后不会自动补班。已有演示库不因跨日被重置；若日后没有所选周，主管按手册手工安排。安装/界面/模拟签到检查与真实门店、物理设备、独立用户及教师验收分开。
