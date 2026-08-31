/* 图 1 · 问题分布：按类别与严重等级（27 项，与问题总表逐一对应） */
(function () {
  var el = document.getElementById('chart-issues');
  if (!el || typeof echarts === 'undefined') return;

  /* [类别, 高, 中, 低] */
  var categories = [
    ['正确性', 3, 1, 0],
    ['玩法逻辑', 0, 3, 1],
    ['架构设计', 0, 2, 1],
    ['代码卫生', 0, 0, 3],
    ['配置与文档', 0, 1, 2],
    ['算法与性能', 1, 1, 0],
    ['可靠性与体验', 0, 2, 0],
    ['提示词工程', 0, 1, 0],
    ['资源管理', 0, 1, 0],
    ['可观测性', 0, 1, 0],
    ['安全', 0, 0, 1],
    ['依赖管理', 0, 0, 1],
    ['工程实践', 1, 0, 0]
  ];
  var names = ['高危（5）', '中危（13）', '低危（9）'];

  var chart = echarts.init(el);
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      formatter: function (p) {
        var row = categories[p.dataIndex];
        var sevNames = ['高危', '中危', '低危'];
        var total = row[1] + row[2] + row[3];
        var lines = ['<b>' + row[0] + '</b>（共 ' + total + ' 项）'];
        for (var s = 0; s < 3; s++) {
          if (row[s + 1] > 0) lines.push(sevNames[s] + '：' + row[s + 1] + ' 项');
        }
        return lines.join('<br/>');
      }
    },
    legend: {
      top: 0,
      itemWidth: 14, itemHeight: 10,
      textStyle: { fontSize: 13, color: '#2b2420' },
      data: names
    },
    grid: { left: 110, right: 40, top: 40, bottom: 10 },
    xAxis: {
      type: 'value',
      max: 4,
      interval: 1,
      name: '问题数',
      nameTextStyle: { fontSize: 12, color: '#7a6f63' },
      axisLabel: { fontSize: 12, color: '#7a6f63' },
      splitLine: { lineStyle: { color: '#e2d9c6', type: 'dashed' } }
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: categories.map(function (c) { return c[0]; }),
      axisLabel: { fontSize: 12.5, color: '#2b2420' },
      axisLine: { lineStyle: { color: '#d9d0bf' } },
      axisTick: { show: false }
    },
    color: ['#c05b4a', '#b8973f', '#8a8175'],
    series: [0, 1, 2].map(function (s) {
      return {
        name: names[s],
        type: 'bar',
        stack: 'total',
        barWidth: '60%',
        itemStyle: { borderRadius: s === 2 ? [0, 3, 3, 0] : 0 },
        label: {
          show: true,
          position: 'inside',
          color: '#fff',
          fontSize: 11.5,
          fontWeight: 600,
          formatter: function (p) { return p.value > 0 ? p.value : ''; }
        },
        data: categories.map(function (c) { return c[s + 1]; })
      };
    })
  });

  window.addEventListener('resize', function () { chart.resize(); });
})();
