// 字幕排版自测：node tests/test_reflow.js
const assert = require('assert');
const R = require('../static/reflow.js');
const W = 50;
const lines = (t, w = W) => R.reflow(t, w).split('\n');

// 合并：英文用空格；中文行尾无标点用空格隔开，有全角标点直接相连
assert.strictEqual(R.merge('We are here\nwith Verity Crawford'), 'We are here with Verity Crawford');
assert.strictEqual(R.merge('能为\n你深爱的人'), '能为 你深爱的人');
assert.strictEqual(R.merge('嗯，有人会说“狠角色”。\n我选这个。'), '嗯，有人会说“狠角色”。我选这个。');
assert.strictEqual(R.merge('- 哦……\n- 让我们体会一下'), '- 哦…… - 让我们体会一下');
assert.strictEqual(R.merge('  single  line  '), 'single line');
assert.strictEqual(R.merge(''), '');

// 不超宽：只有一行
assert.deepStrictEqual(lines('- "On the planet."\n- Yes.'), ['- "On the planet." - Yes.']);
assert.deepStrictEqual(lines('能为\n你深爱的人\n提供如此美好的生活\n是什么感觉？'), ['能为 你深爱的人 提供如此美好的生活 是什么感觉？']);

// 超宽：每行都不超过上限，且在空格 / 标点处断开
for (const t of ['What it is like\nto be able to provide\nsuch a beautiful life for\nthe people you love so much?',
                 'at her home in Vermont,\nthe place she shares with\nher husband and the twins.']) {
  const ls = lines(t);
  assert.ok(ls.length === 2, ls);
  ls.forEach(l => assert.ok(R.textWidth(l) <= W, l));
  assert.strictEqual(ls.join(' '), R.merge(t));                       // 只换行，不丢字
}
// 英文：句末引号之后断开
assert.deepStrictEqual(lines('Uh, some would say "badass."\nI\'ll go with that.', 42), ['Uh, some would say "badass."', "I'll go with that."]);
// 英文：不拆人名，不在冠词/介词后断开，优先在介词前断开
assert.deepStrictEqual(lines('We are here\nwith Verity Crawford\nat her home in Vermont,'), ['We are here with Verity Crawford', 'at her home in Vermont,']);
assert.deepStrictEqual(lines('What it is like\nto be able to provide\nsuch a beautiful life for\nthe people you love so much?'),
  ['What it is like to be able to provide such', 'a beautiful life for the people you love so much?']);
// 多人对话：在说话人之间断开
assert.deepStrictEqual(lines('- Oh...\n- Let\'s take that in\nfor just a second.', 42), ['- Oh...', "- Let's take that in for just a second."]);
// 中文：优先在逗号 / 空格处断开，而不是拆开词语
assert.deepStrictEqual(lines('在她佛蒙特州的家中，\n她和丈夫还有双胞胎\n共同生活的地方。', 42), ['在她佛蒙特州的家中，', '她和丈夫还有双胞胎 共同生活的地方。']);
assert.deepStrictEqual(lines('我们现在\n和维里蒂·克劳福德一起\n在她佛蒙特州的家中，', 42), ['我们现在 和维里蒂·克劳福德一起', '在她佛蒙特州的家中，']);
// 行首不出现标点
for (const l of lines('不存在的罪人是一部改编自科琳·胡佛同名小说的心理惊悚电影，讲述了一个作家的故事，她的丈夫和孩子。', 30)) {
  assert.ok(!/^[，。！？、；：）」』》]/.test(l), l);
}
// 标签不计宽度、不被拆开
assert.strictEqual(R.textWidth('<i>你好</i>'), 4);
assert.deepStrictEqual(lines('<i>A steady flow of crimson\nseeps through his knuckles,</i>', 42), ['<i>A steady flow of crimson', 'seeps through his knuckles,</i>']);
// 无法拆分的超长单词：允许溢出，不死循环
assert.strictEqual(lines('Supercalifragilisticexpialidocious', 10).length, 1);
// 宽度：汉字 2，字母 1
assert.strictEqual(R.textWidth('Hi 你好'), 7);
// 歌词 / 氛围音识别
const L = R.isLyricOrSfx;
for (const t of ['♪ Ooh, baby, baby ♪', '♪ Ooh, baby\nbaby ♪', '♪ line one ♪\n♪ line two ♪', '<i>♪ Hold me closer ♪</i>', '{\\an8}♪ la la ♪',
                 '♪', '♫ Instrumental ♫', '♪ No closing note', '# Happy birthday to you #', '[MUSIC PLAYING]', '[birds chirping]',
                 '(sighs)', '(DOOR CREAKS)', '[音乐]', '（笑声）', '【掌声】', '- [gasps]\n- [screams]', '- ♪ la ♪ - ♪ la la ♪', '♪ ♪', '[music] ♪ la ♪']) {
  assert.ok(L(t), '应识别为歌词/音效：' + JSON.stringify(t));
}
for (const t of ['Hello there.', '(sighs) Fine.', '[Man] Get out!', '♪ la la ♪\n- Stop singing!', '♪ la la - Hey!', '- Oh... - Let\'s go.',
                 'Number #1 fan', 'I love this song ♪', '', '我爱你', 'What is (this)?']) {
  assert.ok(!L(t), '不应识别为歌词/音效：' + JSON.stringify(t));
}
// 双语字幕：识别文件类型，按语言拆成两行
const raw = ['要是大家知道\nIf people knew', '这人和他家人对我做了什么\nwhat this man\nand his family did to me,', '大家就等着看我死呢！\nPeople just waitin\' to see me die!', '我叫 John\nMy name is John', '谢谢\nThanks.'];
assert.strictEqual(R.detectBilingual(raw), 'lines');
assert.strictEqual(R.reflowBilingual(raw[1], 50, 'lines'), '这人和他家人对我做了什么\nwhat this man and his family did to me,');
assert.strictEqual(R.reflowBilingual(raw[3], 50, 'lines'), '我叫 John\nMy name is John');          // 中文里的英文名不被拆走
assert.strictEqual(R.reflowBilingual(raw[4], 50, 'lines'), '谢谢\nThanks.');
assert.strictEqual(R.reflowBilingual('If people knew\n要是大家知道', 50, 'lines'), 'If people knew\n要是大家知道');   // 外文在上也可以
assert.strictEqual(R.reflowBilingual('全是扯淡 It\'s all bullshit.', 50, 'lines'), '全是扯淡\nIt\'s all bullshit.');
// 已经被合并到一行的（之前的版本导入后会变成这样）
const merged = ['要是大家知道 If people knew', '这人和他家人对我做了什么 what this man\nand his family did to me,', '大家就等着看我死呢！\nPeople just waitin\' to see me die!',
  '我知道 人不会放弃希望 I know\nthat you don\'t abandon hope', '你得把豁免协议说成真的 弗雷德 You have\nto sell the immunity, Fred.'];
assert.strictEqual(R.detectBilingual(merged), 'words');
assert.deepStrictEqual(merged.map(t => R.reflowBilingual(t, 50, 'words')), ['要是大家知道\nIf people knew', '这人和他家人对我做了什么\nwhat this man and his family did to me,',
  '大家就等着看我死呢！\nPeople just waitin\' to see me die!', '我知道 人不会放弃希望\nI know that you don\'t abandon hope', '你得把豁免协议说成真的 弗雷德\nYou have to sell the immunity, Fred.']);
assert.strictEqual(R.reflowBilingual('哦！Oh!', 50, 'words'), '哦！\nOh!');
assert.strictEqual(R.reflowBilingual('呃……Um...', 50, 'words'), '呃……\nUm...');
assert.strictEqual(R.splitBilingual('我喜欢iPhone', true, 'words'), null);   // 没有全角标点时不拆
// 每种语言超宽时仍按宽度换行
assert.ok(R.reflowBilingual('确保咱们都能平安度过这事？ make sure we\nget through this thing safe and sound?', 50, 'words').split('\n').every(l => R.textWidth(l) <= 50));
// 单语字幕不会被当成双语
assert.strictEqual(R.detectBilingual(['Hello there.', 'Where are you going?', 'I know that.', 'Fine.']), null);
assert.strictEqual(R.detectBilingual(['你好', '我喜欢 iPhone', '我叫 John', '走吧', '好的']), null);
assert.strictEqual(R.splitBilingual('我喜欢 iPhone', false), null);
// 歌词不参与判断
assert.strictEqual(R.detectBilingual(['♪ la ♪', '♪ la la ♪', '♪ 啦 ♪', '你好\nHello', '谢谢\nThanks']), null);
// 合并：全角标点后接英文时保留空格
assert.strictEqual(R.merge('我知道，\nI know'), '我知道， I know');
assert.strictEqual(R.merge('嗯，有人会说“狠角色”。\n我选这个。'), '嗯，有人会说“狠角色”。我选这个。');
console.log('reflow ok');
