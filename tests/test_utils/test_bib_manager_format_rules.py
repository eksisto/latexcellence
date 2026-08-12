"""bib_manager 格式确定性识别规则测试（离线、不依赖 LLM）。"""

import pytest

from backend.modules.bib_manager.format_rules import detect_format

FORMAT_CASES = [
    (
        "GB/T 7714-2015",
        "张三, 李四, 王五. 基于深度学习的文本生成研究[J]. 计算机学报, 2023, 46(1): 1-15.",
    ),
    (
        "APA 7th",
        "Vaswani, A., Shazeer, N., Parmar, N., Uszkoreit, J., Jones, L., Gomez, A. N., "
        "Kaiser, Ł., & Polosukhin, I. (2017). Attention is all you need. "
        "arXiv preprint arXiv:1706.03762.",
    ),
    (
        "IEEE",
        'J. Smith, J. Jones, and B. Brown, "The impact of neural networks on modern '
        'research," IEEE Transactions on Pattern Analysis and Machine Intelligence, '
        "vol. 42, no. 3, pp. 500-515, 2020.",
    ),
    (
        "MLA 9th",
        'Smith, John, and Jane Jones. "The Impact of Neural Networks on Modern '
        'Research." Journal of Testing, vol. 12, no. 3, 2020, pp. 100-115.',
    ),
    (
        "Chicago",
        'Smith, John, and Jane Jones. 2020. "The Impact of Neural Networks on Modern '
        'Research." Journal of Testing 12, no. 3: 100–115.',
    ),
    (
        "Vancouver",
        "Smith J, Jones J, Brown B. The impact of neural networks on modern research. "
        "J Test. 2020;12(3):100-115.",
    ),
    (
        "Harvard",
        "Smith, J., Jones, J. and Brown, B., 2020. The impact of neural networks on "
        "modern research. Journal of Testing, 12(3), pp.100-115.",
    ),
    (
        "Nature",
        "Smith, J., Jones, J. & Brown, B. The impact of neural networks on modern "
        "research. Nature 580, 100–115 (2020).",
    ),
    (
        "AMA",
        "Smith J, Jones J, Brown B. The impact of neural networks on modern research. "
        "JAMA. 2020;323(10):1000-1005. doi:10.1001/jama.2020.1234",
    ),
    (
        "ACS",
        "Smith, J.; Jones, J.; Brown, B. The Impact of Neural Networks on Modern "
        "Research. J. Am. Chem. Soc. 2020, 142 (10), 1000–1015. "
        "https://doi.org/10.1021/jacs.0c01234",
    ),
    (
        "OSCOLA",
        "John Smith, 'The Impact of Neural Networks on Modern Research' (2020) "
        "12(3) Journal of Testing 100",
    ),
    (
        "Turabian",
        'Smith, John, and Jane Jones. "The Impact of Neural Networks on Modern '
        'Research." Journal of Testing 12, no. 3 (2020): 100–115.',
    ),
    (
        "ACM",
        "John Smith, Jane Jones, and Bob Brown. 2020. The Impact of Neural Networks on "
        "Modern Research. J. ACM 12, 3, Article 45 (2020), 15 pages. "
        "https://doi.org/10.1145/1234567",
    ),
    (
        "APS",
        'J. Smith, J. Jones, and B. Brown, "The Impact of Neural Networks on Modern '
        'Research," Phys. Rev. Lett. 124, 100001 (2020).',
    ),
]


class TestDetectFormat:
    @pytest.mark.parametrize("expected,sample", FORMAT_CASES)
    def test_detects_expected_format(self, expected, sample):
        result = detect_format(sample)
        assert result is not None, f"{expected} 未命中任何规则"
        assert result["format_name"] == expected
        assert result["confidence"] == "high"

    def test_gbt_marker_maps_entry_type(self):
        assert (
            detect_format("张三. 专著[M]. 北京: 出版社, 2020.")["entry_type"] == "book"
        )
        assert (
            detect_format("李四. 会议论文[C]. 北京: 出版社, 2020.")["entry_type"]
            == "inproceedings"
        )
        assert (
            detect_format("王五. 网络文献[EB/OL]. (2020-01-01).")["entry_type"]
            == "misc"
        )

    def test_unknown_format_returns_none(self):
        assert detect_format("Some unusual reference without known markers.") is None
        assert detect_format("") is None
