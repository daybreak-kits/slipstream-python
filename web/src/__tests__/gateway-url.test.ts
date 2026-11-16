import { describe, expect, it } from "vitest";

import { validateGatewayBaseUrl } from "@/lib/settings";

describe("gateway base url validation", () => {
  it("accepts http/https origins and path-prefixed gateways", () => {
    expect(validateGatewayBaseUrl("")).toBeNull();
    expect(validateGatewayBaseUrl("  http://127.0.0.1:3681  ")).toBeNull();
    expect(validateGatewayBaseUrl("https://llm.example.com:8443/")).toBeNull();
    expect(validateGatewayBaseUrl("https://llm.example.com/gateway")).toBeNull();
  });

  it("rejects credentials, query strings, fragments and non-http schemes", () => {
    expect(validateGatewayBaseUrl("ftp://llm.example.com")).toContain("http");
    expect(validateGatewayBaseUrl("llm.example.com:3681")).toContain("完整地址");
    expect(validateGatewayBaseUrl("https://user:pass@llm.example.com")).toContain("账号密码");
    expect(validateGatewayBaseUrl("https://llm.example.com?debug=1")).toContain("查询参数");
    expect(validateGatewayBaseUrl("https://llm.example.com#section")).toContain("片段");
  });
});
