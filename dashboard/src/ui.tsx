import { AnimatePresence, motion, type Variants } from "motion/react";
import type { ComponentProps, ReactNode } from "react";

export const ease = [0.22, 1, 0.36, 1] as const;

export const stagger: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.05, delayChildren: 0.04 } },
};

export const rise: Variants = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.45, ease } },
};

export function Panel(props: ComponentProps<typeof motion.section>) {
  return <motion.section variants={rise} {...props} />;
}

export function Collapse({
  show,
  children,
}: {
  show: boolean;
  children: ReactNode;
}) {
  return (
    <AnimatePresence initial={false}>
      {show && (
        <motion.div
          style={{ overflow: "hidden" }}
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.25, ease }}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function Ticker({ value }: { value: string }) {
  return (
    <span className="metric-value">
      <AnimatePresence initial={false} mode="popLayout">
        <motion.strong
          key={value}
          initial={{ y: "70%", opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: "-70%", opacity: 0 }}
          transition={{ duration: 0.3, ease }}
        >
          {value}
        </motion.strong>
      </AnimatePresence>
    </span>
  );
}
