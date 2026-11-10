import { Button, Card, createTheme, Table } from '@mantine/core';

const FONT_FAMILY = 'Inter, system-ui, -apple-system, "Segoe UI", sans-serif';

export const theme = createTheme({
  primaryColor: 'moss',
  colors: {
    // The app's one accent, a muted moss green. Shade 6 is the filled color in light mode and shade 8 in dark.
    // Both carry white text at AA, and shade 8 is the brighter so it stands out on brown.
    moss: [
      '#f0f5ea',
      '#dfead3',
      '#c4d7ae',
      '#a6c286',
      '#8cae68',
      '#6f9650',
      '#4b6f33',
      '#3f5e2a',
      '#55803a',
      '#3d5a2a',
    ],
    // Mantine reads text from 0, dimmed from 2, borders from 4, cards and inputs from 6, and the shell from 7.
    // A warm espresso ramp: the page (8) is the deepest, the header and sidebar (7) sit one step up, and cards (6) another.
    dark: [
      '#f1ebe4',
      '#d9d0c5',
      '#b3a698',
      '#857a6e',
      '#352c25',
      '#2b241e',
      '#211b16',
      '#1a1511',
      '#14100d',
      '#0f0c0a',
    ],
    // Mantine reads dimmed from 6 in light mode, so it stays dark enough for AA on white and on the cream page.
    gray: [
      '#faf8f5',
      '#f4f0ea',
      '#ebe5dc',
      '#ddd5c9',
      '#c7bdae',
      '#a89d8d',
      '#6e6558',
      '#5c5448',
      '#494238',
      '#352f27',
    ],
  },
  defaultRadius: 'md',
  radius: { xs: '4px', sm: '8px', md: '12px', lg: '16px', xl: '24px' },
  fontFamily: FONT_FAMILY,
  // The app's text is mostly xs and sm, so those steps sit above Mantine's default.
  fontSizes: { xs: '0.875rem', sm: '0.9375rem', md: '1rem', lg: '1.125rem', xl: '1.3125rem' },
  headings: {
    fontFamily: FONT_FAMILY,
    fontWeight: '700',
    sizes: {
      h3: { fontSize: '1.875rem', lineHeight: '1.2' },
    },
  },
  components: {
    Button: Button.extend({ defaultProps: { radius: 'xl' } }),
    Card: Card.extend({ defaultProps: { withBorder: true, padding: 'lg', radius: 'lg' } }),
    Table: Table.extend({ defaultProps: { verticalSpacing: 'xs', fz: 'sm', striped: true } }),
  },
});
