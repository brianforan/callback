import { Box, Group, SegmentedControl, Stack, Table, Text } from '@mantine/core';
import { Fragment, useState, type ReactNode } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { formatWeek, pct } from '../lib/format';
import type { FunnelStage, PostingWeek, ShareWeek, WeeklyPoint } from '../lib/types';
import { FocusCard, useFocusActive } from './Focus';

/** Charts keep a fixed height on the page and fill the available space in focus. */
function useChartHeight(height: number) {
  return useFocusActive() ? '100%' : height;
}

const AXIS_TICK = { fill: 'var(--viz-muted)', fontSize: 13 };
const TOOLTIP_STYLE = {
  background: 'var(--viz-surface)',
  border: '1px solid var(--viz-grid)',
  borderRadius: 8,
  color: 'var(--viz-ink)',
  fontSize: 13,
};

interface ChartCardProps {
  title: string;
  subtitle?: string;
  columns: string[];
  rows: (string | number)[][];
  children: ReactNode;
}

/** A chart with a table twin, so no value is only reachable by hovering. */
export function ChartCard({ title, subtitle, columns, rows, children }: ChartCardProps) {
  const [view, setView] = useState('chart');
  return (
    <FocusCard title={title} h="100%">
      <Group justify="space-between" align="flex-start" mb="sm" wrap="nowrap">
        <Box>
          <Text fw={600} size="sm">
            {title}
          </Text>
          {subtitle && (
            <Text size="xs" c="dimmed">
              {subtitle}
            </Text>
          )}
        </Box>
        <SegmentedControl
          size="xs"
          style={{ flexShrink: 0 }}
          value={view}
          onChange={setView}
          data={[
            { value: 'chart', label: 'Chart' },
            { value: 'table', label: 'Table' },
          ]}
        />
      </Group>
      {view === 'chart' ? (
        <div className="chart-card-body">{children}</div>
      ) : (
        <Table.ScrollContainer minWidth={0} mah={280}>
          <Table>
            <Table.Thead>
              <Table.Tr>
                {columns.map((column) => (
                  <Table.Th key={column}>{column}</Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody className="tabular">
              {rows.map((row, index) => (
                <Table.Tr key={index}>
                  {row.map((cell, cellIndex) => (
                    <Table.Td key={cellIndex}>{cell}</Table.Td>
                  ))}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </FocusCard>
  );
}

export function WeeklyChart({ data }: { data: WeeklyPoint[] }) {
  const height = useChartHeight(260);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} barGap={2} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--viz-grid)" />
        <XAxis
          dataKey="week_start"
          tickFormatter={(value) => formatWeek(String(value))}
          tick={AXIS_TICK}
          axisLine={{ stroke: 'var(--viz-axis)' }}
          tickLine={false}
          minTickGap={24}
        />
        <YAxis allowDecimals={false} tick={AXIS_TICK} axisLine={false} tickLine={false} />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          cursor={{ fill: 'var(--viz-hover)' }}
          labelFormatter={(value) => `Week of ${formatWeek(String(value))}`}
        />
        <Legend
          iconType="circle"
          iconSize={8}
          wrapperStyle={{ fontSize: 13 }}
          formatter={(value) => <span style={{ color: 'var(--viz-muted)' }}>{value}</span>}
        />
        <Bar
          isAnimationActive={false}
          dataKey="applications"
          name="Applications"
          fill="var(--viz-series-1)"
          radius={[4, 4, 0, 0]}
          maxBarSize={16}
        />
        <Bar
          isAnimationActive={false}
          dataKey="callbacks"
          name="Callbacks"
          fill="var(--viz-series-2)"
          radius={[4, 4, 0, 0]}
          maxBarSize={16}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}

const FUNNEL_LABELS: Record<string, string> = {
  applied: 'Applied',
  screening: 'Callback',
  interview: 'Interview',
  offer: 'Offer',
};

export function Funnel({ stages }: { stages: FunnelStage[] }) {
  const top = stages[0]?.count ?? 0;
  return (
    <Stack gap="sm">
      {stages.map((stage, index) => {
        const share = top ? stage.count / top : 0;
        return (
          <Box key={stage.stage}>
            <Group justify="space-between" mb={4}>
              <Text size="xs" c="dimmed">
                {FUNNEL_LABELS[stage.stage] ?? stage.stage}
              </Text>
              <Text size="xs" className="tabular">
                <b>{stage.count}</b>
                {index > 0 && top > 0 ? ` · ${pct(share)} of applied` : ''}
              </Text>
            </Group>
            <div className="funnel-track">
              {stage.count > 0 && (
                <div
                  className="funnel-bar"
                  style={{ width: `${share * 100}%`, background: `var(--viz-funnel-${index + 1})` }}
                />
              )}
            </div>
          </Box>
        );
      })}
    </Stack>
  );
}

export interface RateRow {
  label: string;
  rate: number;
  applications: number;
  callbacks: number;
}

const RATE_LABEL_MAX = 24;

interface RateTickProps {
  x?: number;
  y?: number;
  payload?: { value: string };
}

/** A category tick that ends long names with an ellipsis, with the full name as its tooltip. */
function RateTick({ x = 0, y = 0, payload }: RateTickProps) {
  const label = payload?.value ?? '';
  const text = label.length > RATE_LABEL_MAX ? `${label.slice(0, RATE_LABEL_MAX - 1)}…` : label;
  return (
    <text x={x - 6} y={y} dy={4} textAnchor="end" {...AXIS_TICK}>
      <title>{label}</title>
      {text}
    </text>
  );
}

export function RateBars({ data }: { data: RateRow[] }) {
  const height = useChartHeight(Math.max(140, data.length * 38 + 32));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 56, left: 0, bottom: 0 }}>
        <CartesianGrid horizontal={false} stroke="var(--viz-grid)" />
        <XAxis
          type="number"
          domain={[0, 1]}
          tickFormatter={(value) => pct(Number(value))}
          tick={AXIS_TICK}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          type="category"
          dataKey="label"
          width={170}
          tick={<RateTick />}
          axisLine={{ stroke: 'var(--viz-axis)' }}
          tickLine={false}
        />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          cursor={{ fill: 'var(--viz-hover)' }}
          formatter={(value, _name, item) => [
            `${pct(Number(value))} (${item.payload.callbacks} of ${item.payload.applications})`,
            'Callback rate',
          ]}
        />
        <Bar
          isAnimationActive={false}
          dataKey="rate"
          fill="var(--viz-series-1)"
          radius={[0, 4, 4, 0]}
          maxBarSize={16}
        >
          <LabelList
            dataKey="rate"
            position="right"
            formatter={(value: unknown) => pct(Number(value))}
            style={{ fill: 'var(--viz-muted)', fontSize: 13 }}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// Levels are ordered, so they step along one ramp. Manager is a separate track and takes the
// second series color, and postings with no level recede into the axis color.
const LEVEL_FILLS: Record<string, string> = {
  intern: 'var(--viz-heat-2)',
  junior: 'var(--viz-heat-3)',
  mid: 'var(--viz-heat-4)',
  senior: 'var(--viz-heat-5)',
  staff: 'var(--viz-heat-6)',
  principal: 'var(--viz-heat-7)',
  manager: 'var(--viz-series-2)',
  not_set: 'var(--viz-axis)',
};

function levelName(level: string) {
  if (level === 'not_set') return 'Not set';
  return level.charAt(0).toUpperCase() + level.slice(1);
}

const SINGLE_WEEK_NOTE = 'One week of data so far. The trend appears after a second week.';

interface SingleWeekBar {
  label: string;
  share: number;
  value: string;
}

function SingleWeekBars({ rows }: { rows: SingleWeekBar[] }) {
  return (
    <Stack gap="sm">
      <div className="share-list">
        {rows.map(({ label, share, value }) => (
          <Fragment key={label}>
            <Text size="sm">{label}</Text>
            <div className="share-track">
              <div className="share-fill" style={{ width: `${share * 100}%` }} />
            </div>
            <Text size="sm" ta="right" className="tabular">
              {value}
            </Text>
          </Fragment>
        ))}
      </div>
      <Text size="xs" c="dimmed">
        {SINGLE_WEEK_NOTE}
      </Text>
    </Stack>
  );
}

export function LevelMixChart({ data, levels }: { data: PostingWeek[]; levels: string[] }) {
  const height = useChartHeight(280);
  const weeksWithData = data.filter((week) => levels.some((level) => week.levels[level]));
  if (weeksWithData.length < 2) {
    const counts = weeksWithData[0]?.levels ?? {};
    const total = levels.reduce((sum, level) => sum + (counts[level] ?? 0), 0);
    const rows = levels.map((level) => ({
      label: levelName(level),
      share: total ? (counts[level] ?? 0) / total : 0,
      value: String(counts[level] ?? 0),
    }));
    return <SingleWeekBars rows={rows} />;
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--viz-grid)" />
        <XAxis
          dataKey="week_start"
          tickFormatter={(value) => formatWeek(String(value))}
          tick={AXIS_TICK}
          axisLine={{ stroke: 'var(--viz-axis)' }}
          tickLine={false}
          minTickGap={24}
        />
        <YAxis allowDecimals={false} tick={AXIS_TICK} axisLine={false} tickLine={false} />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          cursor={{ fill: 'var(--viz-hover)' }}
          labelFormatter={(value) => `Week of ${formatWeek(String(value))}`}
        />
        <Legend
          iconType="circle"
          iconSize={8}
          wrapperStyle={{ fontSize: 13 }}
          formatter={(value) => <span style={{ color: 'var(--viz-muted)' }}>{value}</span>}
        />
        {levels.map((level) => (
          <Bar
            key={level}
            isAnimationActive={false}
            dataKey={(week: PostingWeek) => week.levels[level] ?? 0}
            name={levelName(level)}
            stackId="levels"
            fill={LEVEL_FILLS[level]}
            stroke="var(--viz-surface)"
            strokeWidth={2}
            maxBarSize={28}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

export interface WeeklyValue {
  week_start: string;
  value: number | null;
}

interface WeeklyValueChartProps {
  data: WeeklyValue[];
  name: string;
  format: (value: number) => string;
  emptyText: string;
}

export function WeeklyValueChart({ data, name, format, emptyText }: WeeklyValueChartProps) {
  const height = useChartHeight(240);
  const weeksWithData = data.filter((week) => week.value !== null);
  if (!weeksWithData.length) {
    return (
      <Text size="sm" c="dimmed">
        {emptyText}
      </Text>
    );
  }
  if (weeksWithData.length === 1) {
    const [{ week_start: weekStart, value }] = weeksWithData;
    return (
      <Stack gap={4}>
        <Text fz={32} fw={600} lh={1.2} className="tabular">
          {format(value as number)}
        </Text>
        <Text size="sm">
          {name}, week of {formatWeek(weekStart)}
        </Text>
        <Text size="xs" c="dimmed">
          {SINGLE_WEEK_NOTE}
        </Text>
      </Stack>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--viz-grid)" />
        <XAxis
          dataKey="week_start"
          tickFormatter={(value) => formatWeek(String(value))}
          tick={AXIS_TICK}
          axisLine={{ stroke: 'var(--viz-axis)' }}
          tickLine={false}
          minTickGap={24}
        />
        <YAxis
          domain={[0, 'auto']}
          tickFormatter={(value) => format(Number(value))}
          tick={AXIS_TICK}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelFormatter={(value) => `Week of ${formatWeek(String(value))}`}
          formatter={(value) => [format(Number(value)), name]}
        />
        <Line
          isAnimationActive={false}
          type="monotone"
          dataKey="value"
          name={name}
          connectNulls
          stroke="var(--viz-series-1)"
          strokeWidth={2}
          dot={{ r: 4, fill: 'var(--viz-series-1)', stroke: 'var(--viz-surface)', strokeWidth: 2 }}
          activeDot={{ r: 6 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

const TREND_LINE_COLORS = [1, 2, 3, 4, 5].map((slot) => `var(--viz-stack-${slot})`);

// One week can't show a trend, and its dots would all sit on one spot, so it ranks the terms instead.
function SingleWeekShares({ shares, names }: { shares: Record<string, number>; names: string[] }) {
  const rows = names
    .map((name) => ({ name, share: shares[name] ?? 0 }))
    .sort((a, b) => b.share - a.share)
    .map(({ name, share }) => ({ label: name, share, value: pct(share) }));
  return <SingleWeekBars rows={rows} />;
}

// A week with no applications has no shares, which shows as a gap rather than as zero.
export function ShareTrendChart({ data, names }: { data: ShareWeek[]; names: string[] }) {
  const height = useChartHeight(280);
  const weeksWithData = data.filter((week) => Object.keys(week.shares).length);
  if (weeksWithData.length < 2) {
    return <SingleWeekShares shares={weeksWithData[0]?.shares ?? {}} names={names} />;
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 16, left: -12, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--viz-grid)" />
        <XAxis
          dataKey="week_start"
          tickFormatter={(value) => formatWeek(String(value))}
          tick={AXIS_TICK}
          axisLine={{ stroke: 'var(--viz-axis)' }}
          tickLine={false}
          minTickGap={24}
        />
        <YAxis
          domain={[0, 1]}
          tickFormatter={(value) => pct(Number(value))}
          tick={AXIS_TICK}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelFormatter={(value) => `Week of ${formatWeek(String(value))}`}
          formatter={(value, name) => [pct(Number(value)), name]}
        />
        <Legend
          iconType="circle"
          iconSize={8}
          wrapperStyle={{ fontSize: 13 }}
          formatter={(value) => <span style={{ color: 'var(--viz-muted)' }}>{value}</span>}
        />
        {names.map((name, index) => (
          <Line
            key={name}
            isAnimationActive={false}
            type="monotone"
            dataKey={(week: ShareWeek) =>
              Object.keys(week.shares).length ? (week.shares[name] ?? 0) : null
            }
            name={name}
            connectNulls
            stroke={TREND_LINE_COLORS[index]}
            strokeWidth={2}
            dot={{ r: 4, fill: TREND_LINE_COLORS[index], stroke: 'var(--viz-surface)', strokeWidth: 2 }}
            activeDot={{ r: 6 }}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
