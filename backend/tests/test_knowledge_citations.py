from app.knowledge.citations import (
    CITATION_EXCERPT_CHAR_LIMIT,
    compact_knowledge_citation_labels,
    knowledge_citations_from_results,
    restore_truncated_atomic_references,
)


def test_compact_knowledge_citation_labels_renumbers_by_first_appearance() -> None:
    """验证引用按首次出现重编号，并丢弃未被正文引用的来源。"""
    content, citations = compact_knowledge_citation_labels(
        "先参考手册。[4] 再确认规范。[1] 最后仍参考手册。[4]",
        [
            {"id": "kref_1", "label": "[1]", "title": "规范"},
            {"id": "kref_2", "label": "[2]", "title": "无关来源"},
            {"id": "kref_3", "label": "[3]", "title": "另一无关来源"},
            {"id": "kref_4", "label": "[4]", "title": "手册"},
        ],
    )

    assert content == "先参考手册。[1] 再确认规范。[2] 最后仍参考手册。[1]"
    assert [(item["label"], item["title"]) for item in citations] == [
        ("[1]", "手册"),
        ("[2]", "规范"),
    ]


def test_compact_knowledge_citation_labels_supports_historical_filtered_metadata() -> None:
    """验证跳号的历史引用元数据也能按出现顺序连续重编号。"""
    content, citations = compact_knowledge_citation_labels(
        "排查步骤来自手册。[1] 区域故障需要报修。[4]",
        [
            {"id": "kref_1", "label": "[1]", "title": "排查手册"},
            {"id": "kref_4", "label": "[4]", "title": "网络故障"},
        ],
    )

    assert content == "排查步骤来自手册。[1] 区域故障需要报修。[2]"
    assert [item["label"] for item in citations] == ["[1]", "[2]"]


def test_compact_knowledge_citation_labels_preserves_range_order() -> None:
    """验证区间引用按原文顺序编号，并去掉文末重复来源行。"""
    content, citations = compact_knowledge_citation_labels(
        "请假制度依据员工手册。[1]-[4]\n\n参考来源：[1] [2] [3] [4] [5]",
        [
            {"id": f"kref_{index}", "label": f"[{index}]", "title": f"来源 {index}"}
            for index in range(1, 6)
        ],
    )

    assert content == "请假制度依据员工手册。[1]-[4]"
    assert [item["label"] for item in citations] == ["[1]", "[2]", "[3]", "[4]"]


def test_compact_knowledge_citation_labels_removes_unsupported_model_labels() -> None:
    """验证超出真实来源数量的模型引用标签被删除。"""
    content, citations = compact_knowledge_citation_labels(
        "制度正文。[1]\n\n参考来源：[1] [2] [3] [4]",
        [{"id": "kref_1", "label": "[1]", "title": "付款制度"}],
    )

    assert content == "制度正文。[1]"
    assert citations == [{"id": "kref_1", "label": "[1]", "title": "付款制度"}]


def test_compact_knowledge_citation_labels_removes_footer_without_sources() -> None:
    """验证没有来源时整段参考来源页脚被移除。"""
    content, citations = compact_knowledge_citation_labels(
        "没有检索依据的回答。\n\n参考来源：[1] [2]",
        [],
    )

    assert content == "没有检索依据的回答。"
    assert citations == []


def test_compact_knowledge_citation_labels_keeps_sources_without_adding_a_footer() -> None:
    """验证正文不含引用时保持正文不变，仅对来源重编号。"""
    content, citations = compact_knowledge_citation_labels(
        "制度规定七天内可以申请退款。",
        [
            {"id": "kref_1", "label": "[1]", "title": "退款政策"},
            {"id": "kref_4", "label": "[4]", "title": "退款流程"},
        ],
    )

    assert content == "制度规定七天内可以申请退款。"
    assert [(item["label"], item["title"]) for item in citations] == [
        ("[1]", "退款政策"),
        ("[2]", "退款流程"),
    ]


def test_restore_truncated_email_from_unique_cited_evidence() -> None:
    """验证被截断的邮箱能按唯一命中证据补全为完整地址。"""
    reply = "请将材料发送至 ops@example... [1]"
    citations = [
        {
            "label": "[1]",
            "source_path": "employee-guide.md",
            "excerpt": "材料准备完成后发送至 ops@example.test。",
        }
    ]

    assert restore_truncated_atomic_references(reply, citations) == (
        "请将材料发送至 ops@example.test [1]"
    )


def test_restore_truncated_email_keeps_ambiguous_prefix_unchanged() -> None:
    """验证前缀匹配到多个候选时保持原文，不做猜测补全。"""
    reply = "联系 ops@example... [1]"
    citations = [
        {
            "label": "[1]",
            "excerpt": "可联系 ops@example.test 或 ops@example.team。",
        }
    ]

    assert restore_truncated_atomic_references(reply, citations) == reply


def test_knowledge_citations_use_evidence_pack_as_canonical_source() -> None:
    """验证有证据包时以证据为权威引用来源，而非概念摘要。"""
    citations = knowledge_citations_from_results(
        [
            {
                "selected_concepts": [
                    {
                        "concept_id": f"sources/vue3-coding-standards-{index}",
                        "type": "Source Document",
                        "title": f"前端编码规范 {index}",
                        "description": "Vue 3、Vite、TypeScript、组件编写和命名规范。",
                        "source_refs": [{"source_path": "vue3-coding-standards.md"}],
                    }
                    for index in range(4)
                ],
                "evidence_pack": [
                    {
                        "chunk_id": "kchunk_citation_demo",
                        "document_id": "kdoc_citation_demo",
                        "bucket_id": "kbucket_citation_demo",
                        "source_path": "citation-demo.md",
                        "section_path": "知识引用测试说明 / 引用规则",
                        "summary": "回答基于业务资料时必须展示可点击知识引用。",
                        "excerpt": "StaffDeck 引用测试规则。",
                    }
                ],
            }
        ]
    )

    assert len(citations) == 1
    assert citations[0]["kind"] == "evidence"
    assert citations[0]["title"] == "知识引用测试说明 / 引用规则"
    assert citations[0]["source_path"] == "citation-demo.md"


def test_knowledge_citations_fall_back_to_wiki_concepts_without_evidence() -> None:
    """验证缺少证据包时回退到 wiki 概念生成引用。"""
    citations = knowledge_citations_from_results(
        [
            {
                "selected_concepts": [
                    {
                        "concept_id": "sources/vue3-coding-standards",
                        "type": "Source Document",
                        "title": "前端编码规范",
                        "description": "Vue 3、Vite、TypeScript、组件编写和命名规范。",
                        "source_refs": [{"source_path": "vue3-coding-standards.md"}],
                    }
                ]
            }
        ]
    )

    assert citations[0]["kind"] == "concept"
    assert citations[0]["title"] == "前端编码规范"
    assert citations[0]["source_path"] == "vue3-coding-standards.md"


def test_empty_evidence_does_not_block_concept_fallback() -> None:
    """验证证据为空条目时仍能回退到概念引用。"""
    citations = knowledge_citations_from_results(
        [
            {
                "evidence_pack": [{}],
                "selected_concepts": [
                    {
                        "concept_id": "sources/refund-policy",
                        "title": "退款政策",
                        "content": "七天内可申请退款。",
                        "source_refs": [{"source_path": "refund-policy.md"}],
                    }
                ],
            }
        ]
    )

    assert len(citations) == 1
    assert citations[0]["kind"] == "concept"
    assert citations[0]["source_path"] == "refund-policy.md"


def test_knowledge_citations_support_chunks_only_results() -> None:
    """验证只返回 chunks 的历史结果也能生成证据引用。"""
    citations = knowledge_citations_from_results(
        [
            {
                "chunks": [
                    {
                        "id": "chunk_historical",
                        "source_ref": "historical-guide.md",
                        "content": "历史知识结果中的有效证据。",
                    }
                ]
            }
        ]
    )

    assert len(citations) == 1
    assert citations[0]["kind"] == "evidence"
    assert citations[0]["source_path"] == "historical-guide.md"
    assert citations[0]["content"] == "历史知识结果中的有效证据。"


def test_knowledge_citations_fall_back_to_okf_without_evidence_or_concepts() -> None:
    """验证既无证据也无概念时回退到 OKF 引用。"""
    citations = knowledge_citations_from_results(
        [
            {
                "okf_citations": [
                    {
                        "concept_id": "sources/refund-policy",
                        "title": "退款政策",
                        "target": "refund-policy.md",
                        "label": "七天内可申请退款",
                    }
                ]
            }
        ]
    )

    assert citations[0]["kind"] == "okf"
    assert citations[0]["title"] == "退款政策"
    assert citations[0]["source_path"] == "refund-policy.md"


def test_knowledge_citations_apply_source_fallback_per_result() -> None:
    """验证每条结果各自选择来源类型，不互相污染。"""
    citations = knowledge_citations_from_results(
        [
            {
                "evidence_pack": [
                    {
                        "chunk_id": "chunk_policy",
                        "source_path": "policy.pdf",
                        "section_path": "报销制度",
                        "content": "制度证据",
                    }
                ]
            },
            {
                "okf_citations": [
                    {
                        "concept_id": "sources/login-guide",
                        "title": "登录指南",
                        "target": "login.md",
                        "label": "登录步骤",
                    }
                ]
            },
        ],
        max_results=None,
    )

    assert [(item["kind"], item["source_path"]) for item in citations] == [
        ("evidence", "policy.pdf"),
        ("okf", "login.md"),
    ]


def test_knowledge_citations_default_to_latest_result_like_answer_context() -> None:
    """验证默认只取最新一次检索结果，与回答上下文的取值一致。"""
    citations = knowledge_citations_from_results(
        [
            {
                "evidence_pack": [
                    {
                        "chunk_id": "old_chunk",
                        "source_path": "old-policy.pdf",
                        "content": "旧检索结果",
                    }
                ]
            },
            {
                "evidence_pack": [
                    {
                        "chunk_id": "latest_chunk",
                        "source_path": "latest-policy.pdf",
                        "content": "最新检索结果",
                    }
                ]
            },
        ]
    )

    assert len(citations) == 1
    assert citations[0]["source_path"] == "latest-policy.pdf"


def test_knowledge_citations_keep_distinct_chunks_in_the_same_section() -> None:
    """验证同一章节下的不同片段各自保留为独立引用。"""
    citations = knowledge_citations_from_results(
        [
            {
                "evidence_pack": [
                    {
                        "chunk_id": "chunk_1",
                        "section_path": "报销制度",
                        "content": "内容一",
                    },
                    {
                        "chunk_id": "chunk_2",
                        "section_path": "报销制度",
                        "content": "内容二",
                    },
                ]
            }
        ]
    )

    assert [item["chunk_id"] for item in citations] == ["chunk_1", "chunk_2"]


def test_knowledge_citations_keep_distinct_okf_targets_for_one_concept() -> None:
    """验证同一概念下的不同 OKF 目标分别保留为引用。"""
    citations = knowledge_citations_from_results(
        [
            {
                "okf_citations": [
                    {
                        "concept_id": "sources/refund",
                        "target": "refund-a.md",
                        "label": "退款条件 A",
                    },
                    {
                        "concept_id": "sources/refund",
                        "target": "refund-b.md",
                        "label": "退款条件 B",
                    },
                ]
            }
        ]
    )

    assert [item["source_path"] for item in citations] == [
        "refund-a.md",
        "refund-b.md",
    ]


def test_knowledge_citations_use_concept_content_instead_of_summary() -> None:
    """验证概念引用优先使用完整 content，摘要单独保留。"""
    content = "完整 Content 段落。" * 120
    citations = knowledge_citations_from_results(
        [
            {
                "selected_concepts": [
                    {
                        "concept_id": "sources/chatgpt-memory/sections/sec-4",
                        "type": "Source Section",
                        "title": "段落组 1",
                        "description": "段落组 1 摘要，不完整。",
                        "content": content,
                        "source_refs": [{"source_path": "memory.md"}],
                    }
                ],
            }
        ]
    )

    assert citations[0]["content"] == content
    assert citations[0]["excerpt"] == content
    assert citations[0]["summary"] == "段落组 1 摘要，不完整。"


def test_knowledge_citations_keep_long_evidence_excerpt_until_display_limit() -> None:
    """验证未超显示上限的长证据片段原样保留、不被截断。"""
    excerpt = "引用片段" * 900
    citations = knowledge_citations_from_results(
        [
            {
                "evidence_pack": [
                    {
                        "chunk_id": "kchunk_long_excerpt",
                        "document_id": "kdoc_long_excerpt",
                        "bucket_id": "kbucket_long_excerpt",
                        "source_path": "long-citation.md",
                        "section_path": "长引用测试",
                        "summary": "长引用摘要",
                        "excerpt": excerpt,
                    }
                ],
            }
        ]
    )

    assert citations[0]["excerpt"] == excerpt


def test_knowledge_citations_cap_evidence_excerpt_at_display_limit() -> None:
    """验证超过显示上限的证据片段被截断到上限长度。"""
    excerpt = "x" * (CITATION_EXCERPT_CHAR_LIMIT + 16)
    citations = knowledge_citations_from_results(
        [
            {
                "evidence_pack": [
                    {
                        "chunk_id": "kchunk_capped_excerpt",
                        "document_id": "kdoc_capped_excerpt",
                        "bucket_id": "kbucket_capped_excerpt",
                        "source_path": "capped-citation.md",
                        "section_path": "引用上限测试",
                        "summary": "引用上限摘要",
                        "excerpt": excerpt,
                    }
                ],
            }
        ]
    )

    assert citations[0]["excerpt"] == excerpt[:CITATION_EXCERPT_CHAR_LIMIT]
