import React from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { cva } from 'class-variance-authority';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { X } from 'lucide-react';

export const cn=(...args)=>twMerge(clsx(args));
const variants=cva('button',{variants:{variant:{primary:'button-primary',outline:'button-outline',ghost:'button-ghost',danger:'button-danger'},size:{default:'',small:'button-small',icon:'button-icon'}},defaultVariants:{variant:'primary',size:'default'}});
export const Button=React.forwardRef(function Button({variant,size,className,...props},ref){return <button ref={ref} className={cn(variants({variant,size}),className)} {...props}/>});
export function Dialog({open,onOpenChange,title,description,children,wide=false}){return <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}><DialogPrimitive.Portal><DialogPrimitive.Overlay className="dialog-overlay"/><DialogPrimitive.Content className={cn('dialog',wide&&'dialog-wide')}><div className="dialog-header"><div><DialogPrimitive.Title>{title}</DialogPrimitive.Title><DialogPrimitive.Description>{description||'完成下方操作后保存。'}</DialogPrimitive.Description></div><DialogPrimitive.Close asChild><Button variant="ghost" size="icon" aria-label="关闭弹窗"><X size={19}/></Button></DialogPrimitive.Close></div>{children}</DialogPrimitive.Content></DialogPrimitive.Portal></DialogPrimitive.Root>}
