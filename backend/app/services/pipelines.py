"""流水线配置的只读内容生成。

XML 是 Argus 声明式配置（契约见 docs/03-接口与数据契约.md），不是 Jenkins 原生
config.xml；当前仅用于演示 seed 与界面只读预览，不支持导入或编辑，发布治理为
后续独立设计。生成保持确定性，保证 mock 与后端对同一配置输出一致。
"""
from __future__ import annotations


def pipeline_xml(pid: str, name: str, version: str, job: str, arch: str) -> str:
    """按契约生成声明式配置：受控 Job、独占占用、固定参数模板与超时。"""
    return (
        f'<pipeline id="{pid}" name="{name}" version="{version}">\n'
        f'  <jenkins connection="jenkins-main" job="{job}" />\n'
        '  <environment>\n'
        '    <requirements>\n'
        f'      <item name="architecture" value="{arch}" />\n'
        '    </requirements>\n'
        '    <occupancy mode="exclusive" />\n'
        '  </environment>\n'
        '  <parameters>\n'
        '    <parameter name="IMAGE_REF" type="string" required="true" />\n'
        '    <parameter name="TEST_SUITE" type="enum" required="true" default="smoke">\n'
        '      <option value="smoke" />\n'
        '      <option value="network" />\n'
        '    </parameter>\n'
        '  </parameters>\n'
        '  <execution timeoutMinutes="120" />\n'
        '</pipeline>'
    )
