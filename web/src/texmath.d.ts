declare module "markdown-it-texmath" {
  import type MarkdownIt from "markdown-it";

  function texmath(
    md: MarkdownIt,
    options?: {
      engine?: { renderToString: (tex: string, opts?: object) => string };
      delimiters?: string | string[];
      katexOptions?: Record<string, unknown>;
      outerSpace?: boolean;
    },
  ): void;

  export default texmath;
}
