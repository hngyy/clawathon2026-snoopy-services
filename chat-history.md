# Lịch sử chat — GreenNode AgentBase Skills

**Ngày:** 2026-06-11

---

## 1. Tổng quan AgentBase Skills

**Q:** Giải thích AgentBase Skills là gì?

**A:** Repo `greennode-agentbase-skills` là bộ SKILL.md-compatible skills điều khiển toàn bộ vòng đời AI agent trên GreenNode AgentBase. Gồm 10 skill:

| Skill | Vai trò |
|---|---|
| `/agentbase` | Tài liệu tham khảo nền tảng |
| `/agentbase-wizard` | Wizard xây agent từ đầu đến deploy |
| `/agentbase-llm` | Quản lý LLM API key, model |
| `/agentbase-identity` | Danh tính agent + auth đi ra ngoài |
| `/agentbase-memory` | Bộ nhớ agent (short-term + long-term) |
| `/agentbase-gateway` | Resource Gateway (MCP proxy) |
| `/agentbase-policy` | Chính sách phân quyền |
| `/agentbase-deploy` | Deploy + vận hành agent |
| `/agentbase-monitor` | Log, metric, dashboard |
| `/agentbase-teardown` | Dọn dẹp toàn bộ tài nguyên |

---

## 2. Chi tiết agentbase-deploy

**Q:** Tìm hiểu skill agentbase-deploy

**A:** Skill chia làm 4 phần:

### Phần 1 — Deploy Custom Agent (Docker image)
8 bước: kiểm tra tham số → build image → push registry → create/update runtime → chờ ACTIVE → lấy endpoint → test health → báo cáo.

**Bắt buộc:** Container lắng nghe port `8080`, có `GET /health` trả HTTP 200.

**4 biến tự inject (không cần set thủ công):**
- `GREENNODE_CLIENT_ID`
- `GREENNODE_CLIENT_SECRET`
- `GREENNODE_AGENT_IDENTITY`
- `GREENNODE_ENDPOINT_URL`

### Phần 2 — Quản lý Custom Agent Runtime
List, get, update, delete runtime; quản lý endpoint, version, scaling.

### Phần 3 — OpenClaw (Bot Telegram/Zalo)
Template sẵn, không cần Docker image. `gatewayToken` chỉ hiển thị 1 lần sau khi create.

### Phần 4 — Container Registry (CR)
1 repo + 1 credential pair được cấp sẵn. Secret không ghi ra file — pipe in-memory vào `docker login`.

---

## 3. Docker Hub vs AgentBase CR

**Q:** Image của Docker Hub?

| | Docker Hub | AgentBase CR |
|---|---|---|
| Tích hợp platform | Thủ công (file credentials) | Native (`--from-cr`) |
| Secret ra file | Có | Không (in-memory) |
| Tài khoản ngoài | Cần | Không cần |

**Image public Docker Hub:** Dùng thẳng, không cần thêm gì.

**Image private Docker Hub:** Cần file credentials JSON + `--registry-credentials-file`.

```json
{
  "username": "myuser",
  "password": "mypassword",
  "registry": "docker.io",
  "repository": "myorg/myrepo"
}
```

---

## 4. AgentBase CR là gì

- Registry Docker được GreenNode cấp sẵn, 1 repo/user
- Registry URL: `vcr.vngcloud.vn`
- Image path: `vcr.vngcloud.vn/{repoName}/{imageName}:{tag}`
- Cờ `--from-cr` giúp runtime tự lấy credentials khi pull image

---

## 5. Docker image là loại gì

**Q:** Image expose API hay thế nào?

**A:** Bắt buộc phải là **HTTP server**:
- Port: `8080`
- Endpoint: `GET /health` → HTTP 200

Có thể dùng FastAPI thuần hoặc `GreenNodeAgentBaseApp` (wrap sẵn FastAPI + Uvicorn).

---

## 6. agentbase-wizard — 9 bước

| Bước | Tên | Bắt buộc? |
|---|---|---|
| 1 | Check Prerequisites (IAM credentials) | ✅ |
| 2 | Scaffold Project | ✅ |
| 3 | Set Up Memory | ⬜ Tùy chọn |
| 4 | Set Up Identity & External Auth | ⬜ Tùy chọn |
| 5 | Customize Agent Code | ⬜ Tùy chọn |
| 6 | Configure Environment | ✅ |
| 7 | Local Testing | ✅ |
| 8 | Deploy | ✅ |
| 9 | Verify & Next Steps | ✅ |

**Framework có thể chọn ở Bước 2:** Basic, LangChain, LangChain + Memory, LangGraph, LangGraph + Memory.

---

## 7. LangChain vs LangGraph

| | LangChain | LangGraph |
|---|---|---|
| Mô hình | Chuỗi tuyến tính | Đồ thị trạng thái |
| Luồng xử lý | Một chiều | Loop/retry được |
| Độ phức tạp | Thấp | Cao hơn |
| Use case | Q&A, RAG, chatbot đơn giản | ReAct agent, multi-step, self-correction |

**Lộ trình khuyến nghị:** Bắt đầu với LangGraph (không memory) → sau đó thêm memory khi cần cá nhân hóa.

---

## 8. LangGraph là lớp orchestration

LangGraph = **lớp orchestration** — điều phối *khi nào* gọi gì, theo thứ tự nào.

**LangGraph KHÔNG tự làm:**
- Gọi LLM → bạn tự khởi tạo và gọi trong node
- Phát hiện/gọi tool → bạn phải `bind_tools`
- Lưu memory → phải thêm checkpointer
- Xử lý lỗi/retry → tự handle
- Deploy/serve HTTP → cần wrap bằng FastAPI hoặc `GreenNodeAgentBaseApp`

---

## 9. Agent HTTP + Session management

**Agent chạy như HTTP server**, mỗi request là một lượt chat.

| Cấp | Nhớ gì | Dùng khi nào |
|---|---|---|
| Stateless | Không nhớ gì | Q&A độc lập |
| Short-term | Nhớ trong 1 session | Chatbot hội thoại |
| Long-term | Nhớ qua nhiều session | Agent cá nhân hóa |

`session_id` được platform tự inject vào header, SDK expose qua `ctx.session_id`.

---

## 10. LangGraph điều phối theo quy trình + routing đến MCP

**Q:** LangGraph có thể yêu cầu user theo quy trình, hỏi lại nếu thiếu, rồi route đến đúng MCP không?

**A:** Hoàn toàn được — đây là thế mạnh của LangGraph.

### Pattern: Slot filling + Intent routing

```
User input
    │
    ▼
[collect_info]  ←──────────────┐
    │                          │
    ▼                          │
[check_complete] ──── thiếu ───┘
    │ đủ rồi
    ▼
[route_to_mcp]
    ├──→ [mcp_billing]
    ├──→ [mcp_support]
    └──→ [mcp_inventory]
```

**Ví dụ hội thoại:**
```
User:  "tôi muốn đổi gói cước"
Agent: "Bạn đang dùng số điện thoại nào?"
User:  "0901234567"
Agent: "Bạn muốn đổi sang gói nào?"
User:  "gói 200k"
Agent: [đủ rồi] → route → mcp_billing → xử lý
```

**Với AgentBase MCP Gateway:** LangGraph gọi qua AgentBase Resource Gateway (có auth + policy) thay vì gọi thẳng từng MCP server.

```
LangGraph → AgentBase Gateway → MCP Billing / Support / Inventory
```
