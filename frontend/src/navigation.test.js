// @vitest-environment node
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";

// Exercise the deployed edge handler itself: relative assets depend on its redirect.
const terraform = readFileSync(new URL("../../infra/web.tf", import.meta.url), "utf8");
const source = terraform.split('resource "aws_cloudfront_function" "directory_index"')[1]
  .match(/code\s*=\s*<<-EOT\r?\n([\s\S]*?)\r?\n\s*EOT/)[1];
const handler = runInNewContext(`${source}\nhandler`);

describe("static directory navigation", () => {
  it.each(["/app", "/self-hosting", "/pii-redaction/app", "/pii-redaction/self-hosting"])(
    "redirects %s to a trailing slash so relative assets resolve", (uri) => {
      const response = handler({ request: { uri, querystring: {} } });
      expect(response.statusCode).toBe(301);
      expect(new URL(response.headers.location.value, `https://example.com${uri}`).pathname).toBe(`${uri}/`);
    },
  );

  it("retains an outer proxy prefix and repeated, encoded query parameters", () => {
    const response = handler({ request: { uri: "/app", querystring: {
      tag: { value: "a%20b", multiValue: [{ value: "a%20b" }, { value: "c" }] },
    } } });
    const destination = new URL(response.headers.location.value, "https://example.com/pii-redaction/app");
    expect(destination.pathname).toBe("/pii-redaction/app/");
    expect(destination.searchParams.getAll("tag")).toEqual(["a b", "c"]);
  });

  it.each(["/", "/app/", "/self-hosting/"])("serves the index for %s", (uri) => {
    expect(handler({ request: { uri, querystring: {} } }).uri).toBe(`${uri}index.html`);
  });

  it.each(["/assets/app.js", "/taxhance-logo.png", "/self-hosting/index.html"])("preserves the file URL %s", (uri) => {
    expect(handler({ request: { uri, querystring: {} } }).uri).toBe(uri);
  });
});
