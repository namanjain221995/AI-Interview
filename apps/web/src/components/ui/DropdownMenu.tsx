import * as MenuPrimitive from "@radix-ui/react-dropdown-menu";
import { Check, ChevronRight, Dot } from "lucide-react";
import { forwardRef, type ComponentPropsWithoutRef, type ComponentRef, type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export const DropdownMenu = MenuPrimitive.Root;
export const DropdownMenuTrigger = MenuPrimitive.Trigger;
export const DropdownMenuGroup = MenuPrimitive.Group;
export const DropdownMenuSub = MenuPrimitive.Sub;
export const DropdownMenuRadioGroup = MenuPrimitive.RadioGroup;

const contentClass = cn(
  "z-50 min-w-48 overflow-hidden rounded-md border border-line bg-surface-raised p-1 text-fg shadow-overlay outline-none",
  "max-h-(--radix-dropdown-menu-content-available-height) origin-(--radix-dropdown-menu-content-transform-origin)",
  "animate-float-in data-[side=top]:[--float-y:-4px]",
);

const itemClass = cn(
  "relative flex h-8 cursor-default select-none items-center gap-2 rounded-sm px-2 text-sm outline-none transition-colors duration-75",
  "data-[highlighted]:bg-surface-hover data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
  "[&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-fg-muted",
);

export const DropdownMenuContent = forwardRef<
  ComponentRef<typeof MenuPrimitive.Content>,
  ComponentPropsWithoutRef<typeof MenuPrimitive.Content>
>(function DropdownMenuContent({ className, sideOffset = 6, ...props }, ref) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Content ref={ref} sideOffset={sideOffset} collisionPadding={8} className={cn(contentClass, className)} {...props} />
    </MenuPrimitive.Portal>
  );
});

export interface DropdownMenuItemProps extends ComponentPropsWithoutRef<typeof MenuPrimitive.Item> {
  inset?: boolean;
  /** Destructive actions (delete, revoke). */
  destructive?: boolean;
}

export const DropdownMenuItem = forwardRef<ComponentRef<typeof MenuPrimitive.Item>, DropdownMenuItemProps>(function DropdownMenuItem(
  { className, inset, destructive, ...props },
  ref,
) {
  return (
    <MenuPrimitive.Item
      ref={ref}
      className={cn(
        itemClass,
        inset && "pl-8",
        destructive && "text-danger-fg data-[highlighted]:bg-danger-soft [&_svg]:text-danger",
        className,
      )}
      {...props}
    />
  );
});

export const DropdownMenuCheckboxItem = forwardRef<
  ComponentRef<typeof MenuPrimitive.CheckboxItem>,
  ComponentPropsWithoutRef<typeof MenuPrimitive.CheckboxItem>
>(function DropdownMenuCheckboxItem({ className, children, ...props }, ref) {
  return (
    <MenuPrimitive.CheckboxItem ref={ref} className={cn(itemClass, "pl-8", className)} {...props}>
      <span className="absolute left-2 flex size-4 items-center justify-center">
        <MenuPrimitive.ItemIndicator>
          <Check className="text-fg!" strokeWidth={2.25} />
        </MenuPrimitive.ItemIndicator>
      </span>
      {children}
    </MenuPrimitive.CheckboxItem>
  );
});

export const DropdownMenuRadioItem = forwardRef<
  ComponentRef<typeof MenuPrimitive.RadioItem>,
  ComponentPropsWithoutRef<typeof MenuPrimitive.RadioItem>
>(function DropdownMenuRadioItem({ className, children, ...props }, ref) {
  return (
    <MenuPrimitive.RadioItem ref={ref} className={cn(itemClass, "pl-8", className)} {...props}>
      <span className="absolute left-2 flex size-4 items-center justify-center">
        <MenuPrimitive.ItemIndicator>
          <Dot className="size-6! text-fg!" strokeWidth={4} />
        </MenuPrimitive.ItemIndicator>
      </span>
      {children}
    </MenuPrimitive.RadioItem>
  );
});

export const DropdownMenuLabel = forwardRef<
  ComponentRef<typeof MenuPrimitive.Label>,
  ComponentPropsWithoutRef<typeof MenuPrimitive.Label> & { inset?: boolean }
>(function DropdownMenuLabel({ className, inset, ...props }, ref) {
  return <MenuPrimitive.Label ref={ref} className={cn("px-2 pt-1.5 pb-1 text-xs font-medium text-fg-subtle", inset && "pl-8", className)} {...props} />;
});

export const DropdownMenuSeparator = forwardRef<
  ComponentRef<typeof MenuPrimitive.Separator>,
  ComponentPropsWithoutRef<typeof MenuPrimitive.Separator>
>(function DropdownMenuSeparator({ className, ...props }, ref) {
  return <MenuPrimitive.Separator ref={ref} className={cn("-mx-1 my-1 h-px bg-line-subtle", className)} {...props} />;
});

export const DropdownMenuSubTrigger = forwardRef<
  ComponentRef<typeof MenuPrimitive.SubTrigger>,
  ComponentPropsWithoutRef<typeof MenuPrimitive.SubTrigger> & { inset?: boolean }
>(function DropdownMenuSubTrigger({ className, inset, children, ...props }, ref) {
  return (
    <MenuPrimitive.SubTrigger ref={ref} className={cn(itemClass, "data-[state=open]:bg-surface-hover", inset && "pl-8", className)} {...props}>
      {children}
      <ChevronRight className="ml-auto" />
    </MenuPrimitive.SubTrigger>
  );
});

export const DropdownMenuSubContent = forwardRef<
  ComponentRef<typeof MenuPrimitive.SubContent>,
  ComponentPropsWithoutRef<typeof MenuPrimitive.SubContent>
>(function DropdownMenuSubContent({ className, ...props }, ref) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.SubContent ref={ref} className={cn(contentClass, className)} {...props} />
    </MenuPrimitive.Portal>
  );
});

export function DropdownMenuShortcut({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return <span className={cn("ml-auto pl-4 text-xs tracking-wide text-fg-subtle", className)} {...props} />;
}
