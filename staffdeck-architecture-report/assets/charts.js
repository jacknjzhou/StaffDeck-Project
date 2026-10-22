(function () {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg = style.getPropertyValue('--bg').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();

  var monoFamily = "'JetBrains Mono', 'Cascadia Code', monospace";

  // --- Chart: backend module code volume ---
  var modules = [
    ['api', 19382],
    ['core', 14584],
    ['channels', 11911],
    ['db', 6663],
    ['public_api', 4487],
    ['teams', 3652],
    ['knowledge', 3628],
    ['harness', 3513],
    ['llm', 3158],
    ['tools', 3108],
    ['skills', 3002]
  ];

  var chartModules = echarts.init(document.getElementById('chart-modules'), null, { renderer: 'svg' });
  chartModules.setOption({
    animation: false,
    grid: { left: 92, right: 62, top: 8, bottom: 18, containLabel: false },
    tooltip: {
      trigger: 'axis',
      appendToBody: true,
      axisPointer: { type: 'shadow' },
      valueFormatter: function (value) { return value.toLocaleString() + ' 行'; }
    },
    xAxis: {
      type: 'value',
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: muted, fontSize: 11, fontFamily: monoFamily },
      splitLine: { lineStyle: { color: rule } }
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: modules.map(function (item) { return item[0]; }),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: ink, fontSize: 11, fontFamily: monoFamily }
    },
    series: [{
      type: 'bar',
      barWidth: 14,
      data: modules.map(function (item, index) {
        return {
          value: item[1],
          itemStyle: {
            color: index < 3 ? accent : accent2,
            borderRadius: [0, 2, 2, 0]
          }
        };
      }),
      label: {
        show: true,
        position: 'right',
        color: ink,
        fontSize: 11,
        fontFamily: monoFamily,
        formatter: function (p) { return Number(p.value).toLocaleString(); }
      }
    }]
  });
  window.addEventListener('resize', function () { chartModules.resize(); });

  // --- Chart: code asset composition ---
  var composition = [
    ['后端应用', 89408],
    ['后端测试', 66011],
    ['前端源码', 57624]
  ];

  var chartComposition = echarts.init(document.getElementById('chart-composition'), null, { renderer: 'svg' });
  chartComposition.setOption({
    animation: false,
    grid: { left: 70, right: 24, top: 24, bottom: 34, containLabel: false },
    tooltip: {
      trigger: 'axis',
      appendToBody: true,
      axisPointer: { type: 'shadow' },
      valueFormatter: function (value) { return value.toLocaleString() + ' 行'; }
    },
    xAxis: {
      type: 'category',
      data: composition.map(function (item) { return item[0]; }),
      axisLine: { lineStyle: { color: rule } },
      axisTick: { show: false },
      axisLabel: { color: ink, fontSize: 12 }
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: muted, fontSize: 11, fontFamily: monoFamily },
      splitLine: { lineStyle: { color: rule } }
    },
    series: [{
      type: 'bar',
      barWidth: 48,
      data: composition.map(function (item, index) {
        return {
          value: item[1],
          itemStyle: {
            color: index === 0 ? accent : (index === 1 ? accent2 : muted),
            borderRadius: [3, 3, 0, 0]
          }
        };
      }),
      label: {
        show: true,
        position: 'top',
        color: ink,
        fontSize: 11,
        fontFamily: monoFamily,
        formatter: function (p) { return Number(p.value).toLocaleString(); }
      }
    }]
  });
  window.addEventListener('resize', function () { chartComposition.resize(); });

  // --- Mermaid diagrams ---
  if (window.mermaid) {
    mermaid.initialize({
      startOnLoad: true,
      theme: 'base',
      themeVariables: {
        fontFamily: "'Work Sans', 'Microsoft YaHei', 'PingFang SC', sans-serif",
        fontSize: '13px',
        primaryColor: bg2,
        primaryTextColor: ink,
        primaryBorderColor: accent,
        lineColor: muted,
        secondaryColor: bg2,
        tertiaryColor: bg,
        mainBkg: bg2,
        nodeBorder: accent,
        clusterBkg: bg2,
        clusterBorder: rule,
        titleColor: ink,
        edgeLabelBackground: bg,
        actorBkg: bg2,
        actorBorder: accent,
        actorTextColor: ink,
        actorLineColor: rule,
        signalColor: ink,
        signalTextColor: ink,
        labelBoxBkgColor: bg2,
        labelBoxBorderColor: rule,
        labelTextColor: ink,
        loopTextColor: muted,
        noteBkgColor: bg2,
        noteBorderColor: accent2,
        noteTextColor: ink,
        activationBorderColor: accent,
        activationBkgColor: bg2
      }
    });
  }
})();
