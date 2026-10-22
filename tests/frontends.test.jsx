import React from 'react';
import {afterEach, expect, test, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {App} from '../packages/workbench/src/App.jsx';
import {ConnectedApp} from '../apps/workbench/src/ConnectedApp.jsx';

afterEach(()=>{cleanup();vi.unstubAllGlobals();});

test('website shows selected design and working entry action', () => {
  render(<App mode="site"/>);
  expect(screen.getByText(/在同一个聊天室协作/)).toBeTruthy();
  expect(screen.getByText('三步开始使用')).toBeTruthy();
  expect(screen.getByRole('button', {name:/查看演示/})).toBeTruthy();
});

test('workbench opens room creation form', () => {
  render(<App mode="web"/>);
  fireEvent.click(screen.getByRole('button', {name:'新建聊天室'}));
  expect(screen.getByText('给这次协作起个名字，再选择需要的助手。')).toBeTruthy();
  expect(screen.getByPlaceholderText('例如：新产品发布讨论')).toBeTruthy();
});

test('connected workbench starts at real login rather than demo chat', () => {
  render(<ConnectedApp/>);
  expect(screen.getByText('登录工作台')).toBeTruthy();
  expect(screen.getByPlaceholderText('邮箱')).toBeTruthy();
  fireEvent.click(screen.getByText('没有账户？立即注册'));
  expect(screen.getByText('创建账户')).toBeTruthy();
});

test('connected workbench logs in and creates a persisted room through API', async () => {
  const calls=[];
  const dataFor=(url,options)=>{
    if(url.endsWith('/auth/login'))return {access_token:'test-token'};
    if(url.endsWith('/me'))return {id:'user-1',display_name:'测试用户',workspaces:[{id:'workspace-1',role:'OWNER'}]};
    if(url.endsWith('/workspaces/workspace-1/rooms')&&options?.method==='POST')return {id:'room-1',name:'团队讨论',revision:1};
    if(url.endsWith('/workspaces/workspace-1/rooms'))return [];
    if(url.includes('/bots?'))return [];
    if(url.endsWith('/rooms/room-1/bootstrap'))return {messages:[{id:'message-bot-1',message_seq:1,sender_type:'BOT',body:'**加粗结论**\n\n1. 第一项'}],snapshot_seq:1,room:{id:'room-1',name:'团队讨论',revision:1,archived:false}};
    if(url.endsWith('/rooms/room-1/bots'))return [];
    if(url.endsWith('/realtime/tickets'))return {ticket:'test-ticket'};
    if(url.includes('/records?'))return [{id:'message-1',room_id:'room-1',room_name:'团队讨论',message_seq:1,sender_type:'USER',body:'项目计划',created_at:'2026-10-22T00:00:00'}];
    throw new Error(`Unexpected API path: ${url}`);
  };
  vi.stubGlobal('fetch',vi.fn(async(url,options)=>{calls.push([url,options]);return new Response(JSON.stringify({data:dataFor(url,options)}),{status:200,headers:{'Content-Type':'application/json'}});}));
  class FakeSocket {send(){} close(){}}
  vi.stubGlobal('WebSocket',FakeSocket);
  render(<ConnectedApp/>);
  fireEvent.change(screen.getByPlaceholderText('邮箱'),{target:{value:'user@example.com'}});
  fireEvent.change(screen.getByPlaceholderText('密码'),{target:{value:'testing-password-123'}});
  fireEvent.click(screen.getByRole('button',{name:'登录'}));
  await waitFor(()=>expect(screen.getByText('欢迎来到同频 AI')).toBeTruthy());
  fireEvent.click(screen.getByRole('button',{name:'新建聊天室'}));
  fireEvent.change(screen.getByRole('textbox',{name:'名称'}),{target:{value:'团队讨论'}});
  fireEvent.click(screen.getByRole('button',{name:'保存'}));
  await waitFor(()=>expect(screen.getByText('团队消息与 Bot 任务实时同步')).toBeTruthy());
  await waitFor(()=>expect(screen.getByText('加粗结论').tagName).toBe('STRONG'));
  expect(calls.some(([url,options])=>url.endsWith('/workspaces/workspace-1/rooms')&&options?.method==='POST')).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'协作记录'}));
  await waitFor(()=>expect(screen.getByText('项目计划')).toBeTruthy());
});
