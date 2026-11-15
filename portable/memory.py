"""Avoid allocating/initializing a redundant FP32 model before assign=True load."""
_context = 4096
_installed = False

def configure_model(context):
    global _context, _installed
    _context = context
    if _installed:
        return
    import torch
    from fish_speech.models.text2semantic.llama import BaseModelArgs, DualARTransformer, precompute_freqs_cis
    original_config = BaseModelArgs.from_pretrained
    original_init = DualARTransformer.__init__

    def portable_config(path):
        cfg = original_config(path)
        cfg.max_seq_len = _context
        return cfg

    def empty_init(self, config):
        # Upstream from_pretrained subsequently assigns every checkpoint parameter.
        with torch.device('meta'):
            original_init(self, config)
        # These three nonpersistent buffers are absent from the checkpoint, so
        # construct them normally before upstream .to(device, dtype) is invoked.
        with torch.device('cpu'):
            self.freqs_cis = precompute_freqs_cis(config.max_seq_len, config.head_dim, config.rope_base)
            self.fast_freqs_cis = precompute_freqs_cis(config.num_codebooks, config.fast_head_dim, config.rope_base)
            self.causal_mask = torch.tril(torch.ones(config.max_seq_len, config.max_seq_len, dtype=torch.bool))

    BaseModelArgs.from_pretrained = staticmethod(portable_config)
    DualARTransformer.__init__ = empty_init
    _installed = True
