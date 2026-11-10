import { ActionIcon, Button, Card, Group, Modal, Paper, Pill, Text, Tooltip, type CardProps } from '@mantine/core';
import { useWindowEvent } from '@mantine/hooks';
import { IconArrowsMaximize, IconSquare, IconSquareCheck } from '@tabler/icons-react';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';

// Above the app shell, below drawers and other modals, so a row opened from focus shows on top.
const FOCUS_MODAL_Z_INDEX = 150;

interface FocusItem {
  id: string;
  title: string;
}

interface FocusContextValue {
  selected: FocusItem[];
  focused: FocusItem[];
  grid: HTMLDivElement | null;
  toggleSelected: (item: FocusItem) => void;
  focusOn: (items: FocusItem[]) => void;
  release: (id: string) => void;
}

const FocusContext = createContext<FocusContextValue | null>(null);
const FocusActiveContext = createContext(false);

/** True for content currently showing in the focus modal, which has room to fill. */
export function useFocusActive() {
  return useContext(FocusActiveContext);
}

function focusColumns(count: number) {
  if (count <= 3) return count;
  if (count === 4) return 2;
  return count <= 6 ? 3 : 4;
}

export function FocusProvider({ children }: { children: ReactNode }) {
  const [selected, setSelected] = useState<FocusItem[]>([]);
  const [focused, setFocused] = useState<FocusItem[]>([]);
  const [grid, setGrid] = useState<HTMLDivElement | null>(null);
  const opened = focused.length > 0;

  const toggleSelected = useCallback(
    (item: FocusItem) =>
      setSelected((current) =>
        current.some(({ id }) => id === item.id)
          ? current.filter(({ id }) => id !== item.id)
          : [...current, item],
      ),
    [],
  );
  const release = useCallback((id: string) => {
    setSelected((current) => current.filter((item) => item.id !== id));
    setFocused((current) => current.filter((item) => item.id !== id));
  }, []);
  const close = useCallback(() => setFocused([]), []);

  // The modal's own scroll lock blocks wheel events from anything outside its React tree, which
  // is where the moved panels live, so the page behind is locked here instead.
  useEffect(() => {
    if (!opened) return undefined;
    document.documentElement.style.overflow = 'hidden';
    return () => {
      document.documentElement.style.overflow = '';
    };
  }, [opened]);

  // Escape belongs to whatever is on top, so a drawer or open dropdown closes without this too.
  useWindowEvent(
    'keydown',
    (event) => {
      if (event.key !== 'Escape' || event.isComposing || !opened) return;
      const target = event.target as HTMLElement | null;
      if (target?.getAttribute?.('data-mantine-stop-propagation') === 'true') return;
      if (document.querySelectorAll('[role="dialog"]').length > 1) return;
      close();
    },
    { capture: true },
  );

  const value = useMemo(
    () => ({ selected, focused, grid, toggleSelected, focusOn: setFocused, release }),
    [selected, focused, grid, toggleSelected, release],
  );

  return (
    <FocusContext.Provider value={value}>
      {children}

      {selected.length > 0 && !opened && (
        <Paper className="focus-bar" withBorder shadow="md" p="xs">
          <Group gap="sm" wrap="nowrap">
            <Text size="sm" className="tabular">
              {selected.length} selected
            </Text>
            <Button
              size="compact-sm"
              leftSection={<IconArrowsMaximize size={14} />}
              onClick={() => setFocused(selected)}
            >
              Focus
            </Button>
            <Button size="compact-sm" variant="default" onClick={() => setSelected([])}>
              Clear
            </Button>
          </Group>
        </Paper>
      )}

      <Modal.Root
        opened={opened}
        onClose={close}
        fullScreen
        zIndex={FOCUS_MODAL_Z_INDEX}
        lockScroll={false}
        closeOnEscape={false}
        closeOnClickOutside={false}
        transitionProps={{ duration: 0 }}
        classNames={{
          content: 'focus-modal-content',
          header: 'focus-modal-header',
          body: 'focus-modal-body',
        }}
      >
        <Modal.Content aria-label="Focused views">
          <Modal.Header>
            <Group gap={6}>
              {focused.map((item) => (
                <Pill
                  key={item.id}
                  size="md"
                  withRemoveButton={focused.length > 1}
                  removeButtonProps={{ 'aria-label': `Remove ${item.title} from focus` }}
                  onRemove={() => release(item.id)}
                >
                  {item.title}
                </Pill>
              ))}
            </Group>
            <Modal.CloseButton aria-label="Close focus" />
          </Modal.Header>
          <Modal.Body>
            <div
              ref={setGrid}
              className="focus-grid"
              style={{ '--focus-columns': focusColumns(focused.length) } as CSSProperties}
            />
          </Modal.Body>
        </Modal.Content>
      </Modal.Root>
    </FocusContext.Provider>
  );
}

/**
 * A view that can be focused alone or selected to focus with others. Its content always renders
 * into one detached element that moves between the page and the modal, so sorting, the chart or
 * table toggle and scroll position carry across.
 */
export function FocusPanel({ title, children }: { title: string; children: ReactNode }) {
  const context = useContext(FocusContext);
  if (!context) throw new Error('FocusPanel must be rendered inside FocusProvider');
  const { selected, focused, grid, toggleSelected, focusOn, release } = context;

  const id = useId();
  const slot = useRef<HTMLDivElement>(null);
  const [host] = useState(() => {
    const element = document.createElement('div');
    element.className = 'focus-host';
    return element;
  });

  const order = focused.findIndex((item) => item.id === id);
  const active = order >= 0;
  const isSelected = selected.some((item) => item.id === id);

  useLayoutEffect(() => {
    const slotElement = slot.current;
    if (!slotElement) return;
    if (active && grid) {
      // Hold the page's height so closing focus returns to the same scroll position.
      if (host.parentElement === slotElement) {
        slotElement.style.height = `${slotElement.offsetHeight}px`;
      }
      host.style.order = String(order);
      grid.appendChild(host);
    } else {
      slotElement.style.height = '';
      host.style.order = '';
      slotElement.appendChild(host);
    }
  }, [active, grid, host, order]);

  useLayoutEffect(() => () => host.remove(), [host]);
  useEffect(() => () => release(id), [id, release]);

  const item = { id, title };

  return (
    <div ref={slot} className="focus-slot">
      {createPortal(
        <FocusActiveContext.Provider value={active}>
          <div className="focus-panel" data-selected={isSelected && !active}>
            {!active && (
              <div className="focus-controls">
                <Tooltip label={isSelected ? 'Deselect' : 'Select to focus with others'}>
                  <ActionIcon
                    size="sm"
                    variant="subtle"
                    color="gray"
                    aria-label={`Select ${title}`}
                    aria-pressed={isSelected}
                    onClick={() => toggleSelected(item)}
                  >
                    {isSelected ? <IconSquareCheck size={14} /> : <IconSquare size={14} />}
                  </ActionIcon>
                </Tooltip>
                <Tooltip label="Focus">
                  <ActionIcon
                    size="sm"
                    variant="subtle"
                    color="gray"
                    aria-label={`Focus ${title}`}
                    onClick={() => focusOn([item])}
                  >
                    <IconArrowsMaximize size={14} />
                  </ActionIcon>
                </Tooltip>
              </div>
            )}
            {children}
          </div>
        </FocusActiveContext.Provider>,
        host,
      )}
    </div>
  );
}

export function FocusCard({
  title,
  children,
  ...cardProps
}: { title: string; children: ReactNode } & CardProps) {
  return (
    <FocusPanel title={title}>
      <Card {...cardProps}>{children}</Card>
    </FocusPanel>
  );
}
