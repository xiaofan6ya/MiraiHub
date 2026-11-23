import{o as lo,w as Fe,b as go,h as we,k as a,i as bo,E as po,r as M,c as _,t as U,n as mo,p as no,F as Go,D as Jo,j as Yo,H as Qo}from"./vendor-vue-01Qz8dz3.js";import{O as ze,a$ as ro,v as Xo,n as F,p as z,o as L,q as X,r as Pe,ar as Co,T as Ve,a7 as Zo,S as en,a1 as on,y as Ke,a0 as io,z as Ce,W as Ue,b0 as nn,E as V,b1 as tn,k as ln,b2 as rn,x as an,D as se,b3 as ao,m as sn,b4 as cn,b5 as dn,F as un,b6 as hn,as as to,w as vn,A as fn,au as gn,b7 as bn}from"./index-DqZ5b8ag.js";import{N as Qe,u as Xe,D as pn,J as Ee,O as mn,j as Cn,g as Te,w as A,P as so,B as wn,t as yn,k as xn,l as co,q as uo,i as kn,h as Sn,v as On,K as Pn}from"./vendor-naive-SrciwSnN.js";import{N as Fn}from"./FormField.vue_vue_type_style_index_0_scoped_ab743373_lang-B_iDBfNw.js";function wo(e,i){i&&(lo(()=>{const{value:t}=e;t&&Qe.registerHandler(t,i)}),Fe(e,(t,d)=>{d&&Qe.unregisterHandler(d)},{deep:!1}),go(()=>{const{value:t}=e;t&&Qe.unregisterHandler(t)}))}function ho(e){switch(typeof e){case"string":return e||void 0;case"number":return String(e);default:return}}function Ze(e){const i=e.filter(t=>t!==void 0);if(i.length!==0)return i.length===1?i[0]:t=>{e.forEach(d=>{d&&d(t)})}}const Rn=we({name:"Checkmark",render(){return a("svg",{xmlns:"http://www.w3.org/2000/svg",viewBox:"0 0 16 16"},a("g",{fill:"none"},a("path",{d:"M14.046 3.486a.75.75 0 0 1-.032 1.06l-7.93 7.474a.85.85 0 0 1-1.188-.022l-2.68-2.72a.75.75 0 1 1 1.068-1.053l2.234 2.267l7.468-7.038a.75.75 0 0 1 1.06.032z",fill:"currentColor"})))}}),zn=we({props:{onFocus:Function,onBlur:Function},setup(e){return()=>a("div",{style:"width: 0; height: 0",tabindex:0,onFocus:e.onFocus,onBlur:e.onBlur})}}),vo=we({name:"NBaseSelectGroupHeader",props:{clsPrefix:{type:String,required:!0},tmNode:{type:Object,required:!0}},setup(){const{renderLabelRef:e,renderOptionRef:i,labelFieldRef:t,nodePropsRef:d}=bo(ro);return{labelField:t,nodeProps:d,renderLabel:e,renderOption:i}},render(){const{clsPrefix:e,renderLabel:i,renderOption:t,nodeProps:d,tmNode:{rawNode:u}}=this,f=d?.(u),s=i?i(u,!1):ze(u[this.labelField],u,!1),l=a("div",Object.assign({},f,{class:[`${e}-base-select-group-header`,f?.class]}),s);return u.render?u.render({node:l,option:u}):t?t({node:l,option:u,selected:!1}):l}});function Tn(e,i){return a(po,{name:"fade-in-scale-up-transition"},{default:()=>e?a(Xo,{clsPrefix:i,class:`${i}-base-select-option__check`},{default:()=>a(Rn)}):null})}const fo=we({name:"NBaseSelectOption",props:{clsPrefix:{type:String,required:!0},tmNode:{type:Object,required:!0}},setup(e){const{valueRef:i,pendingTmNodeRef:t,multipleRef:d,valueSetRef:u,renderLabelRef:f,renderOptionRef:s,labelFieldRef:l,valueFieldRef:C,showCheckmarkRef:I,nodePropsRef:B,handleOptionClick:y,handleOptionMouseEnter:R}=bo(ro),T=Xe(()=>{const{value:w}=t;return w?e.tmNode.key===w.key:!1});function b(w){const{tmNode:m}=e;m.disabled||y(w,m)}function g(w){const{tmNode:m}=e;m.disabled||R(w,m)}function S(w){const{tmNode:m}=e,{value:E}=T;m.disabled||E||R(w,m)}return{multiple:d,isGrouped:Xe(()=>{const{tmNode:w}=e,{parent:m}=w;return m&&m.rawNode.type==="group"}),showCheckmark:I,nodeProps:B,isPending:T,isSelected:Xe(()=>{const{value:w}=i,{value:m}=d;if(w===null)return!1;const E=e.tmNode.rawNode[C.value];if(m){const{value:j}=u;return j.has(E)}else return w===E}),labelField:l,renderLabel:f,renderOption:s,handleMouseMove:S,handleMouseEnter:g,handleClick:b}},render(){const{clsPrefix:e,tmNode:{rawNode:i},isSelected:t,isPending:d,isGrouped:u,showCheckmark:f,nodeProps:s,renderOption:l,renderLabel:C,handleClick:I,handleMouseEnter:B,handleMouseMove:y}=this,R=Tn(t,e),T=C?[C(i,t),f&&R]:[ze(i[this.labelField],i,t),f&&R],b=s?.(i),g=a("div",Object.assign({},b,{class:[`${e}-base-select-option`,i.class,b?.class,{[`${e}-base-select-option--disabled`]:i.disabled,[`${e}-base-select-option--selected`]:t,[`${e}-base-select-option--grouped`]:u,[`${e}-base-select-option--pending`]:d,[`${e}-base-select-option--show-checkmark`]:f}],style:[b?.style||"",i.style||""],onClick:Ze([I,b?.onClick]),onMouseenter:Ze([B,b?.onMouseenter]),onMousemove:Ze([y,b?.onMousemove])}),a("div",{class:`${e}-base-select-option__content`},T));return i.render?i.render({node:g,option:i,selected:t}):l?l({node:g,option:i,selected:t}):g}}),Mn=F("base-select-menu",`
 line-height: 1.5;
 outline: none;
 z-index: 0;
 position: relative;
 border-radius: var(--n-border-radius);
 transition:
 background-color .3s var(--n-bezier),
 box-shadow .3s var(--n-bezier);
 background-color: var(--n-color);
`,[F("scrollbar",`
 max-height: var(--n-height);
 `),F("virtual-list",`
 max-height: var(--n-height);
 `),F("base-select-option",`
 min-height: var(--n-option-height);
 font-size: var(--n-option-font-size);
 display: flex;
 align-items: center;
 `,[z("content",`
 z-index: 1;
 white-space: nowrap;
 text-overflow: ellipsis;
 overflow: hidden;
 `)]),F("base-select-group-header",`
 min-height: var(--n-option-height);
 font-size: .93em;
 display: flex;
 align-items: center;
 `),F("base-select-menu-option-wrapper",`
 position: relative;
 width: 100%;
 `),z("loading, empty",`
 display: flex;
 padding: 12px 32px;
 flex: 1;
 justify-content: center;
 `),z("loading",`
 color: var(--n-loading-color);
 font-size: var(--n-loading-size);
 `),z("header",`
 padding: 8px var(--n-option-padding-left);
 font-size: var(--n-option-font-size);
 transition: 
 color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
 border-bottom: 1px solid var(--n-action-divider-color);
 color: var(--n-action-text-color);
 `),z("action",`
 padding: 8px var(--n-option-padding-left);
 font-size: var(--n-option-font-size);
 transition: 
 color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
 border-top: 1px solid var(--n-action-divider-color);
 color: var(--n-action-text-color);
 `),F("base-select-group-header",`
 position: relative;
 cursor: default;
 padding: var(--n-option-padding);
 color: var(--n-group-header-text-color);
 `),F("base-select-option",`
 cursor: pointer;
 position: relative;
 padding: var(--n-option-padding);
 transition:
 color .3s var(--n-bezier),
 opacity .3s var(--n-bezier);
 box-sizing: border-box;
 color: var(--n-option-text-color);
 opacity: 1;
 `,[L("show-checkmark",`
 padding-right: calc(var(--n-option-padding-right) + 20px);
 `),X("&::before",`
 content: "";
 position: absolute;
 left: 4px;
 right: 4px;
 top: 0;
 bottom: 0;
 border-radius: var(--n-border-radius);
 transition: background-color .3s var(--n-bezier);
 `),X("&:active",`
 color: var(--n-option-text-color-pressed);
 `),L("grouped",`
 padding-left: calc(var(--n-option-padding-left) * 1.5);
 `),L("pending",[X("&::before",`
 background-color: var(--n-option-color-pending);
 `)]),L("selected",`
 color: var(--n-option-text-color-active);
 `,[X("&::before",`
 background-color: var(--n-option-color-active);
 `),L("pending",[X("&::before",`
 background-color: var(--n-option-color-active-pending);
 `)])]),L("disabled",`
 cursor: not-allowed;
 `,[Pe("selected",`
 color: var(--n-option-text-color-disabled);
 `),L("selected",`
 opacity: var(--n-option-opacity-disabled);
 `)]),z("check",`
 font-size: 16px;
 position: absolute;
 right: calc(var(--n-option-padding-right) - 4px);
 top: calc(50% - 7px);
 color: var(--n-option-check-color);
 transition: color .3s var(--n-bezier);
 `,[Co({enterScale:"0.5"})])])]),In=we({name:"InternalSelectMenu",props:Object.assign(Object.assign({},Ce.props),{clsPrefix:{type:String,required:!0},scrollable:{type:Boolean,default:!0},treeMate:{type:Object,required:!0},multiple:Boolean,size:{type:String,default:"medium"},value:{type:[String,Number,Array],default:null},autoPending:Boolean,virtualScroll:{type:Boolean,default:!0},show:{type:Boolean,default:!0},labelField:{type:String,default:"label"},valueField:{type:String,default:"value"},loading:Boolean,focusable:Boolean,renderLabel:Function,renderOption:Function,nodeProps:Function,showCheckmark:{type:Boolean,default:!0},onMousedown:Function,onScroll:Function,onFocus:Function,onBlur:Function,onKeyup:Function,onKeydown:Function,onTabOut:Function,onMouseenter:Function,onMouseleave:Function,onResize:Function,resetMenuOnOptionsChange:{type:Boolean,default:!0},inlineThemeDisabled:Boolean,scrollbarProps:Object,onToggle:Function}),setup(e){const{mergedClsPrefixRef:i,mergedRtlRef:t,mergedComponentPropsRef:d}=Ke(e),u=io("InternalSelectMenu",t,i),f=Ce("InternalSelectMenu","-internal-select-menu",Mn,nn,e,U(e,"clsPrefix")),s=M(null),l=M(null),C=M(null),I=_(()=>e.treeMate.getFlattenedNodes()),B=_(()=>mn(I.value)),y=M(null);function R(){const{treeMate:r}=e;let h=null;const{value:D}=e;D===null?h=r.getFirstAvailableNode():(e.multiple?h=r.getNode((D||[])[(D||[]).length-1]):h=r.getNode(D),(!h||h.disabled)&&(h=r.getFirstAvailableNode())),Q(h||null)}function T(){const{value:r}=y;r&&!e.treeMate.getNode(r.key)&&(y.value=null)}let b;Fe(()=>e.show,r=>{r?b=Fe(()=>e.treeMate,()=>{e.resetMenuOnOptionsChange?(e.autoPending?R():T(),mo(be)):T()},{immediate:!0}):b?.()},{immediate:!0}),go(()=>{b?.()});const g=_(()=>Cn(f.value.self[V("optionHeight",e.size)])),S=_(()=>Te(f.value.self[V("padding",e.size)])),w=_(()=>e.multiple&&Array.isArray(e.value)?new Set(e.value):new Set),m=_(()=>{const r=I.value;return r&&r.length===0}),E=_(()=>{var r,h;return(h=(r=d?.value)===null||r===void 0?void 0:r.Select)===null||h===void 0?void 0:h.renderEmpty});function j(r){const{onToggle:h}=e;h&&h(r)}function $(r){const{onScroll:h}=e;h&&h(r)}function q(r){var h;(h=C.value)===null||h===void 0||h.sync(),$(r)}function K(){var r;(r=C.value)===null||r===void 0||r.sync()}function ie(){const{value:r}=y;return r||null}function ae(r,h){h.disabled||Q(h,!1)}function G(r,h){h.disabled||j(h)}function Y(r){var h;Ee(r,"action")||(h=e.onKeyup)===null||h===void 0||h.call(e,r)}function Z(r){var h;Ee(r,"action")||(h=e.onKeydown)===null||h===void 0||h.call(e,r)}function N(r){var h;(h=e.onMousedown)===null||h===void 0||h.call(e,r),!e.focusable&&r.preventDefault()}function fe(){const{value:r}=y;r&&Q(r.getNext({loop:!0}),!0)}function ge(){const{value:r}=y;r&&Q(r.getPrev({loop:!0}),!0)}function Q(r,h=!1){y.value=r,h&&be()}function be(){var r,h;const D=y.value;if(!D)return;const le=B.value(D.key);le!==null&&(e.virtualScroll?(r=l.value)===null||r===void 0||r.scrollTo({index:le}):(h=C.value)===null||h===void 0||h.scrollTo({index:le,elSize:g.value}))}function ce(r){var h,D;!((h=s.value)===null||h===void 0)&&h.contains(r.target)&&((D=e.onFocus)===null||D===void 0||D.call(e,r))}function ye(r){var h,D;!((h=s.value)===null||h===void 0)&&h.contains(r.relatedTarget)||(D=e.onBlur)===null||D===void 0||D.call(e,r)}no(ro,{handleOptionMouseEnter:ae,handleOptionClick:G,valueSetRef:w,pendingTmNodeRef:y,nodePropsRef:U(e,"nodeProps"),showCheckmarkRef:U(e,"showCheckmark"),multipleRef:U(e,"multiple"),valueRef:U(e,"value"),renderLabelRef:U(e,"renderLabel"),renderOptionRef:U(e,"renderOption"),labelFieldRef:U(e,"labelField"),valueFieldRef:U(e,"valueField")}),no(tn,s),lo(()=>{const{value:r}=C;r&&r.sync()});const ne=_(()=>{const{size:r}=e,{common:{cubicBezierEaseInOut:h},self:{height:D,borderRadius:le,color:re,groupHeaderTextColor:J,actionDividerColor:pe,optionTextColorPressed:de,optionTextColor:xe,optionTextColorDisabled:ue,optionTextColorActive:Me,optionOpacityDisabled:Ie,optionCheckColor:ke,actionTextColor:Se,optionColorPending:Be,optionColorActive:_e,loadingColor:$e,loadingSize:Re,optionColorActivePending:Oe,[V("optionFontSize",r)]:oe,[V("optionHeight",r)]:n,[V("optionPadding",r)]:v}}=f.value;return{"--n-height":D,"--n-action-divider-color":pe,"--n-action-text-color":Se,"--n-bezier":h,"--n-border-radius":le,"--n-color":re,"--n-option-font-size":oe,"--n-group-header-text-color":J,"--n-option-check-color":ke,"--n-option-color-pending":Be,"--n-option-color-active":_e,"--n-option-color-active-pending":Oe,"--n-option-height":n,"--n-option-opacity-disabled":Ie,"--n-option-text-color":xe,"--n-option-text-color-active":Me,"--n-option-text-color-disabled":ue,"--n-option-text-color-pressed":de,"--n-option-padding":v,"--n-option-padding-left":Te(v,"left"),"--n-option-padding-right":Te(v,"right"),"--n-loading-color":$e,"--n-loading-size":Re}}),{inlineThemeDisabled:ee}=e,te=ee?Ue("internal-select-menu",_(()=>e.size[0]),ne,e):void 0,ve={selfRef:s,next:fe,prev:ge,getPendingTmNode:ie};return wo(s,e.onResize),Object.assign({mergedTheme:f,mergedClsPrefix:i,rtlEnabled:u,virtualListRef:l,scrollbarRef:C,itemSize:g,padding:S,flattenedNodes:I,empty:m,mergedRenderEmpty:E,virtualListContainer(){const{value:r}=l;return r?.listElRef},virtualListContent(){const{value:r}=l;return r?.itemsElRef},doScroll:$,handleFocusin:ce,handleFocusout:ye,handleKeyUp:Y,handleKeyDown:Z,handleMouseDown:N,handleVirtualListResize:K,handleVirtualListScroll:q,cssVars:ee?void 0:ne,themeClass:te?.themeClass,onRender:te?.onRender},ve)},render(){const{$slots:e,virtualScroll:i,clsPrefix:t,mergedTheme:d,themeClass:u,onRender:f}=this;return f?.(),a("div",{ref:"selfRef",tabindex:this.focusable?0:-1,class:[`${t}-base-select-menu`,this.rtlEnabled&&`${t}-base-select-menu--rtl`,u,this.multiple&&`${t}-base-select-menu--multiple`],style:this.cssVars,onFocusin:this.handleFocusin,onFocusout:this.handleFocusout,onKeyup:this.handleKeyUp,onKeydown:this.handleKeyDown,onMousedown:this.handleMouseDown,onMouseenter:this.onMouseenter,onMouseleave:this.onMouseleave},Ve(e.header,s=>s&&a("div",{class:`${t}-base-select-menu__header`,"data-header":!0,key:"header"},s)),this.loading?a("div",{class:`${t}-base-select-menu__loading`},a(Zo,{clsPrefix:t,strokeWidth:20})):this.empty?a("div",{class:`${t}-base-select-menu__empty`,"data-empty":!0},on(e.empty,()=>{var s;return[((s=this.mergedRenderEmpty)===null||s===void 0?void 0:s.call(this))||a(Fn,{theme:d.peers.Empty,themeOverrides:d.peerOverrides.Empty,size:this.size})]})):a(en,Object.assign({ref:"scrollbarRef",theme:d.peers.Scrollbar,themeOverrides:d.peerOverrides.Scrollbar,scrollable:this.scrollable,container:i?this.virtualListContainer:void 0,content:i?this.virtualListContent:void 0,onScroll:i?void 0:this.doScroll},this.scrollbarProps),{default:()=>i?a(pn,{ref:"virtualListRef",class:`${t}-virtual-list`,items:this.flattenedNodes,itemSize:this.itemSize,showScrollbar:!1,paddingTop:this.padding.top,paddingBottom:this.padding.bottom,onResize:this.handleVirtualListResize,onScroll:this.handleVirtualListScroll,itemResizable:!0},{default:({item:s})=>s.isGroup?a(vo,{key:s.key,clsPrefix:t,tmNode:s}):s.ignored?null:a(fo,{clsPrefix:t,key:s.key,tmNode:s})}):a("div",{class:`${t}-base-select-menu-option-wrapper`,style:{paddingTop:this.padding.top,paddingBottom:this.padding.bottom}},this.flattenedNodes.map(s=>s.isGroup?a(vo,{key:s.key,clsPrefix:t,tmNode:s}):a(fo,{clsPrefix:t,key:s.key,tmNode:s})))}),Ve(e.action,s=>s&&[a("div",{class:`${t}-base-select-menu__action`,"data-action":!0,key:"action"},s),a(zn,{onFocus:this.onTabOut,key:"focus-detector"})]))}});function Bn(e){const{textColor2:i,primaryColorHover:t,primaryColorPressed:d,primaryColor:u,infoColor:f,successColor:s,warningColor:l,errorColor:C,baseColor:I,borderColor:B,opacityDisabled:y,tagColor:R,closeIconColor:T,closeIconColorHover:b,closeIconColorPressed:g,borderRadiusSmall:S,fontSizeMini:w,fontSizeTiny:m,fontSizeSmall:E,fontSizeMedium:j,heightMini:$,heightTiny:q,heightSmall:K,heightMedium:ie,closeColorHover:ae,closeColorPressed:G,buttonColor2Hover:Y,buttonColor2Pressed:Z,fontWeightStrong:N}=e;return Object.assign(Object.assign({},rn),{closeBorderRadius:S,heightTiny:$,heightSmall:q,heightMedium:K,heightLarge:ie,borderRadius:S,opacityDisabled:y,fontSizeTiny:w,fontSizeSmall:m,fontSizeMedium:E,fontSizeLarge:j,fontWeightStrong:N,textColorCheckable:i,textColorHoverCheckable:i,textColorPressedCheckable:i,textColorChecked:I,colorCheckable:"#0000",colorHoverCheckable:Y,colorPressedCheckable:Z,colorChecked:u,colorCheckedHover:t,colorCheckedPressed:d,border:`1px solid ${B}`,textColor:i,color:R,colorBordered:"rgb(250, 250, 252)",closeIconColor:T,closeIconColorHover:b,closeIconColorPressed:g,closeColorHover:ae,closeColorPressed:G,borderPrimary:`1px solid ${A(u,{alpha:.3})}`,textColorPrimary:u,colorPrimary:A(u,{alpha:.12}),colorBorderedPrimary:A(u,{alpha:.1}),closeIconColorPrimary:u,closeIconColorHoverPrimary:u,closeIconColorPressedPrimary:u,closeColorHoverPrimary:A(u,{alpha:.12}),closeColorPressedPrimary:A(u,{alpha:.18}),borderInfo:`1px solid ${A(f,{alpha:.3})}`,textColorInfo:f,colorInfo:A(f,{alpha:.12}),colorBorderedInfo:A(f,{alpha:.1}),closeIconColorInfo:f,closeIconColorHoverInfo:f,closeIconColorPressedInfo:f,closeColorHoverInfo:A(f,{alpha:.12}),closeColorPressedInfo:A(f,{alpha:.18}),borderSuccess:`1px solid ${A(s,{alpha:.3})}`,textColorSuccess:s,colorSuccess:A(s,{alpha:.12}),colorBorderedSuccess:A(s,{alpha:.1}),closeIconColorSuccess:s,closeIconColorHoverSuccess:s,closeIconColorPressedSuccess:s,closeColorHoverSuccess:A(s,{alpha:.12}),closeColorPressedSuccess:A(s,{alpha:.18}),borderWarning:`1px solid ${A(l,{alpha:.35})}`,textColorWarning:l,colorWarning:A(l,{alpha:.15}),colorBorderedWarning:A(l,{alpha:.12}),closeIconColorWarning:l,closeIconColorHoverWarning:l,closeIconColorPressedWarning:l,closeColorHoverWarning:A(l,{alpha:.12}),closeColorPressedWarning:A(l,{alpha:.18}),borderError:`1px solid ${A(C,{alpha:.23})}`,textColorError:C,colorError:A(C,{alpha:.1}),colorBorderedError:A(C,{alpha:.08}),closeIconColorError:C,closeIconColorHoverError:C,closeIconColorPressedError:C,closeColorHoverError:A(C,{alpha:.12}),closeColorPressedError:A(C,{alpha:.18})})}const _n={common:ln,self:Bn},$n={color:Object,type:{type:String,default:"default"},round:Boolean,size:{type:String,default:"medium"},closable:Boolean,disabled:{type:Boolean,default:void 0}},En=F("tag",`
 --n-close-margin: var(--n-close-margin-top) var(--n-close-margin-right) var(--n-close-margin-bottom) var(--n-close-margin-left);
 white-space: nowrap;
 position: relative;
 box-sizing: border-box;
 cursor: default;
 display: inline-flex;
 align-items: center;
 flex-wrap: nowrap;
 padding: var(--n-padding);
 border-radius: var(--n-border-radius);
 color: var(--n-text-color);
 background-color: var(--n-color);
 transition: 
 border-color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 color .3s var(--n-bezier),
 box-shadow .3s var(--n-bezier),
 opacity .3s var(--n-bezier);
 line-height: 1;
 height: var(--n-height);
 font-size: var(--n-font-size);
`,[L("strong",`
 font-weight: var(--n-font-weight-strong);
 `),z("border",`
 pointer-events: none;
 position: absolute;
 left: 0;
 right: 0;
 top: 0;
 bottom: 0;
 border-radius: inherit;
 border: var(--n-border);
 transition: border-color .3s var(--n-bezier);
 `),z("icon",`
 display: flex;
 margin: 0 4px 0 0;
 color: var(--n-text-color);
 transition: color .3s var(--n-bezier);
 font-size: var(--n-avatar-size-override);
 `),z("avatar",`
 display: flex;
 margin: 0 6px 0 0;
 `),z("close",`
 margin: var(--n-close-margin);
 transition:
 background-color .3s var(--n-bezier),
 color .3s var(--n-bezier);
 `),L("round",`
 padding: 0 calc(var(--n-height) / 3);
 border-radius: calc(var(--n-height) / 2);
 `,[z("icon",`
 margin: 0 4px 0 calc((var(--n-height) - 8px) / -2);
 `),z("avatar",`
 margin: 0 6px 0 calc((var(--n-height) - 8px) / -2);
 `),L("closable",`
 padding: 0 calc(var(--n-height) / 4) 0 calc(var(--n-height) / 3);
 `)]),L("icon, avatar",[L("round",`
 padding: 0 calc(var(--n-height) / 3) 0 calc(var(--n-height) / 2);
 `)]),L("disabled",`
 cursor: not-allowed !important;
 opacity: var(--n-opacity-disabled);
 `),L("checkable",`
 cursor: pointer;
 box-shadow: none;
 color: var(--n-text-color-checkable);
 background-color: var(--n-color-checkable);
 `,[Pe("disabled",[X("&:hover","background-color: var(--n-color-hover-checkable);",[Pe("checked","color: var(--n-text-color-hover-checkable);")]),X("&:active","background-color: var(--n-color-pressed-checkable);",[Pe("checked","color: var(--n-text-color-pressed-checkable);")])]),L("checked",`
 color: var(--n-text-color-checked);
 background-color: var(--n-color-checked);
 `,[Pe("disabled",[X("&:hover","background-color: var(--n-color-checked-hover);"),X("&:active","background-color: var(--n-color-checked-pressed);")])])])]),Nn=Object.assign(Object.assign(Object.assign({},Ce.props),$n),{bordered:{type:Boolean,default:void 0},checked:Boolean,checkable:Boolean,strong:Boolean,triggerClickOnClose:Boolean,onClose:[Array,Function],onMouseenter:Function,onMouseleave:Function,"onUpdate:checked":Function,onUpdateChecked:Function,internalCloseFocusable:{type:Boolean,default:!0},internalCloseIsButtonTag:{type:Boolean,default:!0},onCheckedChange:Function}),An=sn("n-tag"),eo=we({name:"Tag",props:Nn,slots:Object,setup(e){const i=M(null),{mergedBorderedRef:t,mergedClsPrefixRef:d,inlineThemeDisabled:u,mergedRtlRef:f,mergedComponentPropsRef:s}=Ke(e),l=_(()=>{var g,S;return e.size||((S=(g=s?.value)===null||g===void 0?void 0:g.Tag)===null||S===void 0?void 0:S.size)||"medium"}),C=Ce("Tag","-tag",En,_n,e,d);no(An,{roundRef:U(e,"round")});function I(){if(!e.disabled&&e.checkable){const{checked:g,onCheckedChange:S,onUpdateChecked:w,"onUpdate:checked":m}=e;w&&w(!g),m&&m(!g),S&&S(!g)}}function B(g){if(e.triggerClickOnClose||g.stopPropagation(),!e.disabled){const{onClose:S}=e;S&&se(S,g)}}const y={setTextContent(g){const{value:S}=i;S&&(S.textContent=g)}},R=io("Tag",f,d),T=_(()=>{const{type:g,color:{color:S,textColor:w}={}}=e,m=l.value,{common:{cubicBezierEaseInOut:E},self:{padding:j,closeMargin:$,borderRadius:q,opacityDisabled:K,textColorCheckable:ie,textColorHoverCheckable:ae,textColorPressedCheckable:G,textColorChecked:Y,colorCheckable:Z,colorHoverCheckable:N,colorPressedCheckable:fe,colorChecked:ge,colorCheckedHover:Q,colorCheckedPressed:be,closeBorderRadius:ce,fontWeightStrong:ye,[V("colorBordered",g)]:ne,[V("closeSize",m)]:ee,[V("closeIconSize",m)]:te,[V("fontSize",m)]:ve,[V("height",m)]:r,[V("color",g)]:h,[V("textColor",g)]:D,[V("border",g)]:le,[V("closeIconColor",g)]:re,[V("closeIconColorHover",g)]:J,[V("closeIconColorPressed",g)]:pe,[V("closeColorHover",g)]:de,[V("closeColorPressed",g)]:xe}}=C.value,ue=Te($);return{"--n-font-weight-strong":ye,"--n-avatar-size-override":`calc(${r} - 8px)`,"--n-bezier":E,"--n-border-radius":q,"--n-border":le,"--n-close-icon-size":te,"--n-close-color-pressed":xe,"--n-close-color-hover":de,"--n-close-border-radius":ce,"--n-close-icon-color":re,"--n-close-icon-color-hover":J,"--n-close-icon-color-pressed":pe,"--n-close-icon-color-disabled":re,"--n-close-margin-top":ue.top,"--n-close-margin-right":ue.right,"--n-close-margin-bottom":ue.bottom,"--n-close-margin-left":ue.left,"--n-close-size":ee,"--n-color":S||(t.value?ne:h),"--n-color-checkable":Z,"--n-color-checked":ge,"--n-color-checked-hover":Q,"--n-color-checked-pressed":be,"--n-color-hover-checkable":N,"--n-color-pressed-checkable":fe,"--n-font-size":ve,"--n-height":r,"--n-opacity-disabled":K,"--n-padding":j,"--n-text-color":w||D,"--n-text-color-checkable":ie,"--n-text-color-checked":Y,"--n-text-color-hover-checkable":ae,"--n-text-color-pressed-checkable":G}}),b=u?Ue("tag",_(()=>{let g="";const{type:S,color:{color:w,textColor:m}={}}=e;return g+=S[0],g+=l.value[0],w&&(g+=`a${ao(w)}`),m&&(g+=`b${ao(m)}`),t.value&&(g+="c"),g}),T,e):void 0;return Object.assign(Object.assign({},y),{rtlEnabled:R,mergedClsPrefix:d,contentRef:i,mergedBordered:t,handleClick:I,handleCloseClick:B,cssVars:u?void 0:T,themeClass:b?.themeClass,onRender:b?.onRender})},render(){var e,i;const{mergedClsPrefix:t,rtlEnabled:d,closable:u,color:{borderColor:f}={},round:s,onRender:l,$slots:C}=this;l?.();const I=Ve(C.avatar,y=>y&&a("div",{class:`${t}-tag__avatar`},y)),B=Ve(C.icon,y=>y&&a("div",{class:`${t}-tag__icon`},y));return a("div",{class:[`${t}-tag`,this.themeClass,{[`${t}-tag--rtl`]:d,[`${t}-tag--strong`]:this.strong,[`${t}-tag--disabled`]:this.disabled,[`${t}-tag--checkable`]:this.checkable,[`${t}-tag--checked`]:this.checkable&&this.checked,[`${t}-tag--round`]:s,[`${t}-tag--avatar`]:I,[`${t}-tag--icon`]:B,[`${t}-tag--closable`]:u}],style:this.cssVars,onClick:this.handleClick,onMouseenter:this.onMouseenter,onMouseleave:this.onMouseleave},B||I,a("span",{class:`${t}-tag__content`,ref:"contentRef"},(i=(e=this.$slots).default)===null||i===void 0?void 0:i.call(e)),!this.checkable&&u?a(an,{clsPrefix:t,class:`${t}-tag__close`,disabled:this.disabled,onClick:this.handleCloseClick,focusable:this.internalCloseFocusable,round:s,isButtonTag:this.internalCloseIsButtonTag,absolute:!0}):null,!this.checkable&&this.mergedBordered?a("div",{class:`${t}-tag__border`,style:{borderColor:f}}):null)}}),Dn=X([F("base-selection",`
 --n-padding-single: var(--n-padding-single-top) var(--n-padding-single-right) var(--n-padding-single-bottom) var(--n-padding-single-left);
 --n-padding-multiple: var(--n-padding-multiple-top) var(--n-padding-multiple-right) var(--n-padding-multiple-bottom) var(--n-padding-multiple-left);
 position: relative;
 z-index: auto;
 box-shadow: none;
 width: 100%;
 max-width: 100%;
 display: inline-block;
 vertical-align: bottom;
 border-radius: var(--n-border-radius);
 min-height: var(--n-height);
 line-height: 1.5;
 font-size: var(--n-font-size);
 `,[F("base-loading",`
 color: var(--n-loading-color);
 `),F("base-selection-tags","min-height: var(--n-height);"),z("border, state-border",`
 position: absolute;
 left: 0;
 right: 0;
 top: 0;
 bottom: 0;
 pointer-events: none;
 border: var(--n-border);
 border-radius: inherit;
 transition:
 box-shadow .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
 `),z("state-border",`
 z-index: 1;
 border-color: #0000;
 `),F("base-suffix",`
 cursor: pointer;
 position: absolute;
 top: 50%;
 transform: translateY(-50%);
 right: 10px;
 `,[z("arrow",`
 font-size: var(--n-arrow-size);
 color: var(--n-arrow-color);
 transition: color .3s var(--n-bezier);
 `)]),F("base-selection-overlay",`
 display: flex;
 align-items: center;
 white-space: nowrap;
 pointer-events: none;
 position: absolute;
 top: 0;
 right: 0;
 bottom: 0;
 left: 0;
 padding: var(--n-padding-single);
 transition: color .3s var(--n-bezier);
 `,[z("wrapper",`
 flex-basis: 0;
 flex-grow: 1;
 overflow: hidden;
 text-overflow: ellipsis;
 `)]),F("base-selection-placeholder",`
 color: var(--n-placeholder-color);
 `,[z("inner",`
 max-width: 100%;
 overflow: hidden;
 `)]),F("base-selection-tags",`
 cursor: pointer;
 outline: none;
 box-sizing: border-box;
 position: relative;
 z-index: auto;
 display: flex;
 padding: var(--n-padding-multiple);
 flex-wrap: wrap;
 align-items: center;
 width: 100%;
 vertical-align: bottom;
 background-color: var(--n-color);
 border-radius: inherit;
 transition:
 color .3s var(--n-bezier),
 box-shadow .3s var(--n-bezier),
 background-color .3s var(--n-bezier);
 `),F("base-selection-label",`
 height: var(--n-height);
 display: inline-flex;
 width: 100%;
 vertical-align: bottom;
 cursor: pointer;
 outline: none;
 z-index: auto;
 box-sizing: border-box;
 position: relative;
 transition:
 color .3s var(--n-bezier),
 box-shadow .3s var(--n-bezier),
 background-color .3s var(--n-bezier);
 border-radius: inherit;
 background-color: var(--n-color);
 align-items: center;
 `,[F("base-selection-input",`
 font-size: inherit;
 line-height: inherit;
 outline: none;
 cursor: pointer;
 box-sizing: border-box;
 border:none;
 width: 100%;
 padding: var(--n-padding-single);
 background-color: #0000;
 color: var(--n-text-color);
 transition: color .3s var(--n-bezier);
 caret-color: var(--n-caret-color);
 `,[z("content",`
 text-overflow: ellipsis;
 overflow: hidden;
 white-space: nowrap; 
 `)]),z("render-label",`
 color: var(--n-text-color);
 `)]),Pe("disabled",[X("&:hover",[z("state-border",`
 box-shadow: var(--n-box-shadow-hover);
 border: var(--n-border-hover);
 `)]),L("focus",[z("state-border",`
 box-shadow: var(--n-box-shadow-focus);
 border: var(--n-border-focus);
 `)]),L("active",[z("state-border",`
 box-shadow: var(--n-box-shadow-active);
 border: var(--n-border-active);
 `),F("base-selection-label","background-color: var(--n-color-active);"),F("base-selection-tags","background-color: var(--n-color-active);")])]),L("disabled","cursor: not-allowed;",[z("arrow",`
 color: var(--n-arrow-color-disabled);
 `),F("base-selection-label",`
 cursor: not-allowed;
 background-color: var(--n-color-disabled);
 `,[F("base-selection-input",`
 cursor: not-allowed;
 color: var(--n-text-color-disabled);
 `),z("render-label",`
 color: var(--n-text-color-disabled);
 `)]),F("base-selection-tags",`
 cursor: not-allowed;
 background-color: var(--n-color-disabled);
 `),F("base-selection-placeholder",`
 cursor: not-allowed;
 color: var(--n-placeholder-color-disabled);
 `)]),F("base-selection-input-tag",`
 height: calc(var(--n-height) - 6px);
 line-height: calc(var(--n-height) - 6px);
 outline: none;
 display: none;
 position: relative;
 margin-bottom: 3px;
 max-width: 100%;
 vertical-align: bottom;
 `,[z("input",`
 font-size: inherit;
 font-family: inherit;
 min-width: 1px;
 padding: 0;
 background-color: #0000;
 outline: none;
 border: none;
 max-width: 100%;
 overflow: hidden;
 width: 1em;
 line-height: inherit;
 cursor: pointer;
 color: var(--n-text-color);
 caret-color: var(--n-caret-color);
 `),z("mirror",`
 position: absolute;
 left: 0;
 top: 0;
 white-space: pre;
 visibility: hidden;
 user-select: none;
 -webkit-user-select: none;
 opacity: 0;
 `)]),["warning","error"].map(e=>L(`${e}-status`,[z("state-border",`border: var(--n-border-${e});`),Pe("disabled",[X("&:hover",[z("state-border",`
 box-shadow: var(--n-box-shadow-hover-${e});
 border: var(--n-border-hover-${e});
 `)]),L("active",[z("state-border",`
 box-shadow: var(--n-box-shadow-active-${e});
 border: var(--n-border-active-${e});
 `),F("base-selection-label",`background-color: var(--n-color-active-${e});`),F("base-selection-tags",`background-color: var(--n-color-active-${e});`)]),L("focus",[z("state-border",`
 box-shadow: var(--n-box-shadow-focus-${e});
 border: var(--n-border-focus-${e});
 `)])])]))]),F("base-selection-popover",`
 margin-bottom: -3px;
 display: flex;
 flex-wrap: wrap;
 margin-right: -8px;
 `),F("base-selection-tag-wrapper",`
 max-width: 100%;
 display: inline-flex;
 padding: 0 7px 3px 0;
 `,[X("&:last-child","padding-right: 0;"),F("tag",`
 font-size: 14px;
 max-width: 100%;
 `,[z("content",`
 line-height: 1.25;
 text-overflow: ellipsis;
 overflow: hidden;
 `)])])]),jn=we({name:"InternalSelection",props:Object.assign(Object.assign({},Ce.props),{clsPrefix:{type:String,required:!0},bordered:{type:Boolean,default:void 0},active:Boolean,pattern:{type:String,default:""},placeholder:String,selectedOption:{type:Object,default:null},selectedOptions:{type:Array,default:null},labelField:{type:String,default:"label"},valueField:{type:String,default:"value"},multiple:Boolean,filterable:Boolean,clearable:Boolean,disabled:Boolean,size:{type:String,default:"medium"},loading:Boolean,autofocus:Boolean,showArrow:{type:Boolean,default:!0},inputProps:Object,focused:Boolean,renderTag:Function,onKeydown:Function,onClick:Function,onBlur:Function,onFocus:Function,onDeleteOption:Function,maxTagCount:[String,Number],ellipsisTagPopoverProps:Object,onClear:Function,onPatternInput:Function,onPatternFocus:Function,onPatternBlur:Function,renderLabel:Function,status:String,inlineThemeDisabled:Boolean,ignoreComposition:{type:Boolean,default:!0},onResize:Function}),setup(e){const{mergedClsPrefixRef:i,mergedRtlRef:t}=Ke(e),d=io("InternalSelection",t,i),u=M(null),f=M(null),s=M(null),l=M(null),C=M(null),I=M(null),B=M(null),y=M(null),R=M(null),T=M(null),b=M(!1),g=M(!1),S=M(!1),w=Ce("InternalSelection","-internal-selection",Dn,hn,e,U(e,"clsPrefix")),m=_(()=>e.clearable&&!e.disabled&&(S.value||e.active)),E=_(()=>e.selectedOption?e.renderTag?e.renderTag({option:e.selectedOption,handleClose:()=>{}}):e.renderLabel?e.renderLabel(e.selectedOption,!0):ze(e.selectedOption[e.labelField],e.selectedOption,!0):e.placeholder),j=_(()=>{const n=e.selectedOption;if(n)return n[e.labelField]}),$=_(()=>e.multiple?!!(Array.isArray(e.selectedOptions)&&e.selectedOptions.length):e.selectedOption!==null);function q(){var n;const{value:v}=u;if(v){const{value:H}=f;H&&(H.style.width=`${v.offsetWidth}px`,e.maxTagCount!=="responsive"&&((n=R.value)===null||n===void 0||n.sync({showAllItemsBeforeCalculate:!1})))}}function K(){const{value:n}=T;n&&(n.style.display="none")}function ie(){const{value:n}=T;n&&(n.style.display="inline-block")}Fe(U(e,"active"),n=>{n||K()}),Fe(U(e,"pattern"),()=>{e.multiple&&mo(q)});function ae(n){const{onFocus:v}=e;v&&v(n)}function G(n){const{onBlur:v}=e;v&&v(n)}function Y(n){const{onDeleteOption:v}=e;v&&v(n)}function Z(n){const{onClear:v}=e;v&&v(n)}function N(n){const{onPatternInput:v}=e;v&&v(n)}function fe(n){var v;(!n.relatedTarget||!(!((v=s.value)===null||v===void 0)&&v.contains(n.relatedTarget)))&&ae(n)}function ge(n){var v;!((v=s.value)===null||v===void 0)&&v.contains(n.relatedTarget)||G(n)}function Q(n){Z(n)}function be(){S.value=!0}function ce(){S.value=!1}function ye(n){!e.active||!e.filterable||n.target!==f.value&&n.preventDefault()}function ne(n){Y(n)}const ee=M(!1);function te(n){if(n.key==="Backspace"&&!ee.value&&!e.pattern.length){const{selectedOptions:v}=e;v?.length&&ne(v[v.length-1])}}let ve=null;function r(n){const{value:v}=u;if(v){const H=n.target.value;v.textContent=H,q()}e.ignoreComposition&&ee.value?ve=n:N(n)}function h(){ee.value=!0}function D(){ee.value=!1,e.ignoreComposition&&N(ve),ve=null}function le(n){var v;g.value=!0,(v=e.onPatternFocus)===null||v===void 0||v.call(e,n)}function re(n){var v;g.value=!1,(v=e.onPatternBlur)===null||v===void 0||v.call(e,n)}function J(){var n,v;if(e.filterable)g.value=!1,(n=I.value)===null||n===void 0||n.blur(),(v=f.value)===null||v===void 0||v.blur();else if(e.multiple){const{value:H}=l;H?.blur()}else{const{value:H}=C;H?.blur()}}function pe(){var n,v,H;e.filterable?(g.value=!1,(n=I.value)===null||n===void 0||n.focus()):e.multiple?(v=l.value)===null||v===void 0||v.focus():(H=C.value)===null||H===void 0||H.focus()}function de(){const{value:n}=f;n&&(ie(),n.focus())}function xe(){const{value:n}=f;n&&n.blur()}function ue(n){const{value:v}=B;v&&v.setTextContent(`+${n}`)}function Me(){const{value:n}=y;return n}function Ie(){return f.value}let ke=null;function Se(){ke!==null&&window.clearTimeout(ke)}function Be(){e.active||(Se(),ke=window.setTimeout(()=>{$.value&&(b.value=!0)},100))}function _e(){Se()}function $e(n){n||(Se(),b.value=!1)}Fe($,n=>{n||(b.value=!1)}),lo(()=>{Jo(()=>{const n=I.value;n&&(e.disabled?n.removeAttribute("tabindex"):n.tabIndex=g.value?-1:0)})}),wo(s,e.onResize);const{inlineThemeDisabled:Re}=e,Oe=_(()=>{const{size:n}=e,{common:{cubicBezierEaseInOut:v},self:{fontWeight:H,borderRadius:qe,color:Ge,placeholderColor:Ne,textColor:Ae,paddingSingle:De,paddingMultiple:Je,caretColor:Ye,colorDisabled:je,textColorDisabled:me,placeholderColorDisabled:o,colorActive:c,boxShadowFocus:p,boxShadowActive:O,boxShadowHover:x,border:k,borderFocus:P,borderHover:W,borderActive:he,arrowColor:xo,arrowColorDisabled:ko,loadingColor:So,colorActiveWarning:Oo,boxShadowFocusWarning:Po,boxShadowActiveWarning:Fo,boxShadowHoverWarning:Ro,borderWarning:zo,borderFocusWarning:To,borderHoverWarning:Mo,borderActiveWarning:Io,colorActiveError:Bo,boxShadowFocusError:_o,boxShadowActiveError:$o,boxShadowHoverError:Eo,borderError:No,borderFocusError:Ao,borderHoverError:Do,borderActiveError:jo,clearColor:Lo,clearColorHover:Ho,clearColorPressed:Vo,clearSize:Wo,arrowSize:Ko,[V("height",n)]:Uo,[V("fontSize",n)]:qo}}=w.value,Le=Te(De),He=Te(Je);return{"--n-bezier":v,"--n-border":k,"--n-border-active":he,"--n-border-focus":P,"--n-border-hover":W,"--n-border-radius":qe,"--n-box-shadow-active":O,"--n-box-shadow-focus":p,"--n-box-shadow-hover":x,"--n-caret-color":Ye,"--n-color":Ge,"--n-color-active":c,"--n-color-disabled":je,"--n-font-size":qo,"--n-height":Uo,"--n-padding-single-top":Le.top,"--n-padding-multiple-top":He.top,"--n-padding-single-right":Le.right,"--n-padding-multiple-right":He.right,"--n-padding-single-left":Le.left,"--n-padding-multiple-left":He.left,"--n-padding-single-bottom":Le.bottom,"--n-padding-multiple-bottom":He.bottom,"--n-placeholder-color":Ne,"--n-placeholder-color-disabled":o,"--n-text-color":Ae,"--n-text-color-disabled":me,"--n-arrow-color":xo,"--n-arrow-color-disabled":ko,"--n-loading-color":So,"--n-color-active-warning":Oo,"--n-box-shadow-focus-warning":Po,"--n-box-shadow-active-warning":Fo,"--n-box-shadow-hover-warning":Ro,"--n-border-warning":zo,"--n-border-focus-warning":To,"--n-border-hover-warning":Mo,"--n-border-active-warning":Io,"--n-color-active-error":Bo,"--n-box-shadow-focus-error":_o,"--n-box-shadow-active-error":$o,"--n-box-shadow-hover-error":Eo,"--n-border-error":No,"--n-border-focus-error":Ao,"--n-border-hover-error":Do,"--n-border-active-error":jo,"--n-clear-size":Wo,"--n-clear-color":Lo,"--n-clear-color-hover":Ho,"--n-clear-color-pressed":Vo,"--n-arrow-size":Ko,"--n-font-weight":H}}),oe=Re?Ue("internal-selection",_(()=>e.size[0]),Oe,e):void 0;return{mergedTheme:w,mergedClearable:m,mergedClsPrefix:i,rtlEnabled:d,patternInputFocused:g,filterablePlaceholder:E,label:j,selected:$,showTagsPanel:b,isComposing:ee,counterRef:B,counterWrapperRef:y,patternInputMirrorRef:u,patternInputRef:f,selfRef:s,multipleElRef:l,singleElRef:C,patternInputWrapperRef:I,overflowRef:R,inputTagElRef:T,handleMouseDown:ye,handleFocusin:fe,handleClear:Q,handleMouseEnter:be,handleMouseLeave:ce,handleDeleteOption:ne,handlePatternKeyDown:te,handlePatternInputInput:r,handlePatternInputBlur:re,handlePatternInputFocus:le,handleMouseEnterCounter:Be,handleMouseLeaveCounter:_e,handleFocusout:ge,handleCompositionEnd:D,handleCompositionStart:h,onPopoverUpdateShow:$e,focus:pe,focusInput:de,blur:J,blurInput:xe,updateCounter:ue,getCounter:Me,getTail:Ie,renderLabel:e.renderLabel,cssVars:Re?void 0:Oe,themeClass:oe?.themeClass,onRender:oe?.onRender}},render(){const{status:e,multiple:i,size:t,disabled:d,filterable:u,maxTagCount:f,bordered:s,clsPrefix:l,ellipsisTagPopoverProps:C,onRender:I,renderTag:B,renderLabel:y}=this;I?.();const R=f==="responsive",T=typeof f=="number",b=R||T,g=a(cn,null,{default:()=>a(dn,{clsPrefix:l,loading:this.loading,showArrow:this.showArrow,showClear:this.mergedClearable&&this.selected,onClear:this.handleClear},{default:()=>{var w,m;return(m=(w=this.$slots).arrow)===null||m===void 0?void 0:m.call(w)}})});let S;if(i){const{labelField:w}=this,m=N=>a("div",{class:`${l}-base-selection-tag-wrapper`,key:N.value},B?B({option:N,handleClose:()=>{this.handleDeleteOption(N)}}):a(eo,{size:t,closable:!N.disabled,disabled:d,onClose:()=>{this.handleDeleteOption(N)},internalCloseIsButtonTag:!1,internalCloseFocusable:!1},{default:()=>y?y(N,!0):ze(N[w],N,!0)})),E=()=>(T?this.selectedOptions.slice(0,f):this.selectedOptions).map(m),j=u?a("div",{class:`${l}-base-selection-input-tag`,ref:"inputTagElRef",key:"__input-tag__"},a("input",Object.assign({},this.inputProps,{ref:"patternInputRef",tabindex:-1,disabled:d,value:this.pattern,autofocus:this.autofocus,class:`${l}-base-selection-input-tag__input`,onBlur:this.handlePatternInputBlur,onFocus:this.handlePatternInputFocus,onKeydown:this.handlePatternKeyDown,onInput:this.handlePatternInputInput,onCompositionstart:this.handleCompositionStart,onCompositionend:this.handleCompositionEnd})),a("span",{ref:"patternInputMirrorRef",class:`${l}-base-selection-input-tag__mirror`},this.pattern)):null,$=R?()=>a("div",{class:`${l}-base-selection-tag-wrapper`,ref:"counterWrapperRef"},a(eo,{size:t,ref:"counterRef",onMouseenter:this.handleMouseEnterCounter,onMouseleave:this.handleMouseLeaveCounter,disabled:d})):void 0;let q;if(T){const N=this.selectedOptions.length-f;N>0&&(q=a("div",{class:`${l}-base-selection-tag-wrapper`,key:"__counter__"},a(eo,{size:t,ref:"counterRef",onMouseenter:this.handleMouseEnterCounter,disabled:d},{default:()=>`+${N}`})))}const K=R?u?a(so,{ref:"overflowRef",updateCounter:this.updateCounter,getCounter:this.getCounter,getTail:this.getTail,style:{width:"100%",display:"flex",overflow:"hidden"}},{default:E,counter:$,tail:()=>j}):a(so,{ref:"overflowRef",updateCounter:this.updateCounter,getCounter:this.getCounter,style:{width:"100%",display:"flex",overflow:"hidden"}},{default:E,counter:$}):T&&q?E().concat(q):E(),ie=b?()=>a("div",{class:`${l}-base-selection-popover`},R?E():this.selectedOptions.map(m)):void 0,ae=b?Object.assign({show:this.showTagsPanel,trigger:"hover",overlap:!0,placement:"top",width:"trigger",onUpdateShow:this.onPopoverUpdateShow,theme:this.mergedTheme.peers.Popover,themeOverrides:this.mergedTheme.peerOverrides.Popover},C):null,Y=(this.selected?!1:this.active?!this.pattern&&!this.isComposing:!0)?a("div",{class:`${l}-base-selection-placeholder ${l}-base-selection-overlay`},a("div",{class:`${l}-base-selection-placeholder__inner`},this.placeholder)):null,Z=u?a("div",{ref:"patternInputWrapperRef",class:`${l}-base-selection-tags`},K,R?null:j,g):a("div",{ref:"multipleElRef",class:`${l}-base-selection-tags`,tabindex:d?void 0:0},K,g);S=a(Go,null,b?a(un,Object.assign({},ae,{scrollable:!0,style:"max-height: calc(var(--v-target-height) * 6.6);"}),{trigger:()=>Z,default:ie}):Z,Y)}else if(u){const w=this.pattern||this.isComposing,m=this.active?!w:!this.selected,E=this.active?!1:this.selected;S=a("div",{ref:"patternInputWrapperRef",class:`${l}-base-selection-label`,title:this.patternInputFocused?void 0:ho(this.label)},a("input",Object.assign({},this.inputProps,{ref:"patternInputRef",class:`${l}-base-selection-input`,value:this.active?this.pattern:"",placeholder:"",readonly:d,disabled:d,tabindex:-1,autofocus:this.autofocus,onFocus:this.handlePatternInputFocus,onBlur:this.handlePatternInputBlur,onInput:this.handlePatternInputInput,onCompositionstart:this.handleCompositionStart,onCompositionend:this.handleCompositionEnd})),E?a("div",{class:`${l}-base-selection-label__render-label ${l}-base-selection-overlay`,key:"input"},a("div",{class:`${l}-base-selection-overlay__wrapper`},B?B({option:this.selectedOption,handleClose:()=>{}}):y?y(this.selectedOption,!0):ze(this.label,this.selectedOption,!0))):null,m?a("div",{class:`${l}-base-selection-placeholder ${l}-base-selection-overlay`,key:"placeholder"},a("div",{class:`${l}-base-selection-overlay__wrapper`},this.filterablePlaceholder)):null,g)}else S=a("div",{ref:"singleElRef",class:`${l}-base-selection-label`,tabindex:this.disabled?void 0:0},this.label!==void 0?a("div",{class:`${l}-base-selection-input`,title:ho(this.label),key:"input"},a("div",{class:`${l}-base-selection-input__content`},B?B({option:this.selectedOption,handleClose:()=>{}}):y?y(this.selectedOption,!0):ze(this.label,this.selectedOption,!0))):a("div",{class:`${l}-base-selection-placeholder ${l}-base-selection-overlay`,key:"placeholder"},a("div",{class:`${l}-base-selection-placeholder__inner`},this.placeholder)),g);return a("div",{ref:"selfRef",class:[`${l}-base-selection`,this.rtlEnabled&&`${l}-base-selection--rtl`,this.themeClass,e&&`${l}-base-selection--${e}-status`,{[`${l}-base-selection--active`]:this.active,[`${l}-base-selection--selected`]:this.selected||this.active&&this.pattern,[`${l}-base-selection--disabled`]:this.disabled,[`${l}-base-selection--multiple`]:this.multiple,[`${l}-base-selection--focus`]:this.focused}],style:this.cssVars,onClick:this.onClick,onMouseenter:this.handleMouseEnter,onMouseleave:this.handleMouseLeave,onKeydown:this.onKeydown,onFocusin:this.handleFocusin,onFocusout:this.handleFocusout,onMousedown:this.handleMouseDown},S,s?a("div",{class:`${l}-base-selection__border`}):null,s?a("div",{class:`${l}-base-selection__state-border`}):null)}});function We(e){return e.type==="group"}function yo(e){return e.type==="ignored"}function oo(e,i){try{return!!(1+i.toString().toLowerCase().indexOf(e.trim().toLowerCase()))}catch{return!1}}function Ln(e,i){return{getIsGroup:We,getIgnored:yo,getKey(d){return We(d)?d.name||d.key||"key-required":d[e]},getChildren(d){return d[i]}}}function Hn(e,i,t,d){if(!i)return e;function u(f){if(!Array.isArray(f))return[];const s=[];for(const l of f)if(We(l)){const C=u(l[d]);C.length&&s.push(Object.assign({},l,{[d]:C}))}else{if(yo(l))continue;i(t,l)&&s.push(l)}return s}return u(e)}function Vn(e,i,t){const d=new Map;return e.forEach(u=>{We(u)?u[t].forEach(f=>{d.set(f[i],f)}):d.set(u[i],u)}),d}const Wn=X([F("select",`
 z-index: auto;
 outline: none;
 width: 100%;
 position: relative;
 font-weight: var(--n-font-weight);
 `),F("select-menu",`
 margin: 4px 0;
 box-shadow: var(--n-menu-box-shadow);
 `,[Co({originalTransition:"background-color .3s var(--n-bezier), box-shadow .3s var(--n-bezier)"})])]),Kn=Object.assign(Object.assign({},Ce.props),{to:to.propTo,bordered:{type:Boolean,default:void 0},clearable:Boolean,clearCreatedOptionsOnClear:{type:Boolean,default:!0},clearFilterAfterSelect:{type:Boolean,default:!0},options:{type:Array,default:()=>[]},defaultValue:{type:[String,Number,Array],default:null},keyboard:{type:Boolean,default:!0},value:[String,Number,Array],placeholder:String,menuProps:Object,multiple:Boolean,size:String,menuSize:{type:String},filterable:Boolean,disabled:{type:Boolean,default:void 0},remote:Boolean,loading:Boolean,filter:Function,placement:{type:String,default:"bottom-start"},widthMode:{type:String,default:"trigger"},tag:Boolean,onCreate:Function,fallbackOption:{type:[Function,Boolean],default:void 0},show:{type:Boolean,default:void 0},showArrow:{type:Boolean,default:!0},maxTagCount:[Number,String],ellipsisTagPopoverProps:Object,consistentMenuWidth:{type:Boolean,default:!0},virtualScroll:{type:Boolean,default:!0},labelField:{type:String,default:"label"},valueField:{type:String,default:"value"},childrenField:{type:String,default:"children"},renderLabel:Function,renderOption:Function,renderTag:Function,"onUpdate:value":[Function,Array],inputProps:Object,nodeProps:Function,ignoreComposition:{type:Boolean,default:!0},showOnFocus:Boolean,onUpdateValue:[Function,Array],onBlur:[Function,Array],onClear:[Function,Array],onFocus:[Function,Array],onScroll:[Function,Array],onSearch:[Function,Array],onUpdateShow:[Function,Array],"onUpdate:show":[Function,Array],displayDirective:{type:String,default:"show"},resetMenuOnOptionsChange:{type:Boolean,default:!0},status:String,showCheckmark:{type:Boolean,default:!0},scrollbarProps:Object,onChange:[Function,Array],items:Array}),Yn=we({name:"Select",props:Kn,slots:Object,setup(e){const{mergedClsPrefixRef:i,mergedBorderedRef:t,namespaceRef:d,inlineThemeDisabled:u}=Ke(e),f=Ce("Select","-select",Wn,bn,e,i),s=M(e.defaultValue),l=U(e,"value"),C=uo(l,s),I=M(!1),B=M(""),y=On(e,["items","options"]),R=M([]),T=M([]),b=_(()=>T.value.concat(R.value).concat(y.value)),g=_(()=>{const{filter:o}=e;if(o)return o;const{labelField:c,valueField:p}=e;return(O,x)=>{if(!x)return!1;const k=x[c];if(typeof k=="string")return oo(O,k);const P=x[p];return typeof P=="string"?oo(O,P):typeof P=="number"?oo(O,String(P)):!1}}),S=_(()=>{if(e.remote)return y.value;{const{value:o}=b,{value:c}=B;return!c.length||!e.filterable?o:Hn(o,g.value,c,e.childrenField)}}),w=_(()=>{const{valueField:o,childrenField:c}=e,p=Ln(o,c);return Pn(S.value,p)}),m=_(()=>Vn(b.value,e.valueField,e.childrenField)),E=M(!1),j=uo(U(e,"show"),E),$=M(null),q=M(null),K=M(null),{localeRef:ie}=vn("Select"),ae=_(()=>{var o;return(o=e.placeholder)!==null&&o!==void 0?o:ie.value.placeholder}),G=[],Y=M(new Map),Z=_(()=>{const{fallbackOption:o}=e;if(o===void 0){const{labelField:c,valueField:p}=e;return O=>({[c]:String(O),[p]:O})}return o===!1?!1:c=>Object.assign(o(c),{value:c})});function N(o){const c=e.remote,{value:p}=Y,{value:O}=m,{value:x}=Z,k=[];return o.forEach(P=>{if(O.has(P))k.push(O.get(P));else if(c&&p.has(P))k.push(p.get(P));else if(x){const W=x(P);W&&k.push(W)}}),k}const fe=_(()=>{if(e.multiple){const{value:o}=C;return Array.isArray(o)?N(o):[]}return null}),ge=_(()=>{const{value:o}=C;return!e.multiple&&!Array.isArray(o)?o===null?null:N([o])[0]||null:null}),Q=fn(e),{mergedSizeRef:be,mergedDisabledRef:ce,mergedStatusRef:ye}=Q;function ne(o,c){const{onChange:p,"onUpdate:value":O,onUpdateValue:x}=e,{nTriggerFormChange:k,nTriggerFormInput:P}=Q;p&&se(p,o,c),x&&se(x,o,c),O&&se(O,o,c),s.value=o,k(),P()}function ee(o){const{onBlur:c}=e,{nTriggerFormBlur:p}=Q;c&&se(c,o),p()}function te(){const{onClear:o}=e;o&&se(o)}function ve(o){const{onFocus:c,showOnFocus:p}=e,{nTriggerFormFocus:O}=Q;c&&se(c,o),O(),p&&re()}function r(o){const{onSearch:c}=e;c&&se(c,o)}function h(o){const{onScroll:c}=e;c&&se(c,o)}function D(){var o;const{remote:c,multiple:p}=e;if(c){const{value:O}=Y;if(p){const{valueField:x}=e;(o=fe.value)===null||o===void 0||o.forEach(k=>{O.set(k[x],k)})}else{const x=ge.value;x&&O.set(x[e.valueField],x)}}}function le(o){const{onUpdateShow:c,"onUpdate:show":p}=e;c&&se(c,o),p&&se(p,o),E.value=o}function re(){ce.value||(le(!0),E.value=!0,e.filterable&&De())}function J(){le(!1)}function pe(){B.value="",T.value=G}const de=M(!1);function xe(){e.filterable&&(de.value=!0)}function ue(){e.filterable&&(de.value=!1,j.value||pe())}function Me(){ce.value||(j.value?e.filterable?De():J():re())}function Ie(o){var c,p;!((p=(c=K.value)===null||c===void 0?void 0:c.selfRef)===null||p===void 0)&&p.contains(o.relatedTarget)||(I.value=!1,ee(o),J())}function ke(o){ve(o),I.value=!0}function Se(){I.value=!0}function Be(o){var c;!((c=$.value)===null||c===void 0)&&c.$el.contains(o.relatedTarget)||(I.value=!1,ee(o),J())}function _e(){var o;(o=$.value)===null||o===void 0||o.focus(),J()}function $e(o){var c;j.value&&(!((c=$.value)===null||c===void 0)&&c.$el.contains(Sn(o))||J())}function Re(o){if(!Array.isArray(o))return[];if(Z.value)return Array.from(o);{const{remote:c}=e,{value:p}=m;if(c){const{value:O}=Y;return o.filter(x=>p.has(x)||O.has(x))}else return o.filter(O=>p.has(O))}}function Oe(o){oe(o.rawNode)}function oe(o){if(ce.value)return;const{tag:c,remote:p,clearFilterAfterSelect:O,valueField:x}=e;if(c&&!p){const{value:k}=T,P=k[0]||null;if(P){const W=R.value;W.length?W.push(P):R.value=[P],T.value=G}}if(p&&Y.value.set(o[x],o),e.multiple){const k=Re(C.value),P=k.findIndex(W=>W===o[x]);if(~P){if(k.splice(P,1),c&&!p){const W=n(o[x]);~W&&(R.value.splice(W,1),O&&(B.value=""))}}else k.push(o[x]),O&&(B.value="");ne(k,N(k))}else{if(c&&!p){const k=n(o[x]);~k?R.value=[R.value[k]]:R.value=G}Ae(),J(),ne(o[x],o)}}function n(o){return R.value.findIndex(p=>p[e.valueField]===o)}function v(o){j.value||re();const{value:c}=o.target;B.value=c;const{tag:p,remote:O}=e;if(r(c),p&&!O){if(!c){T.value=G;return}const{onCreate:x}=e,k=x?x(c):{[e.labelField]:c,[e.valueField]:c},{valueField:P,labelField:W}=e;y.value.some(he=>he[P]===k[P]||he[W]===k[W])||R.value.some(he=>he[P]===k[P]||he[W]===k[W])?T.value=G:T.value=[k]}}function H(o){o.stopPropagation();const{multiple:c,tag:p,remote:O,clearCreatedOptionsOnClear:x}=e;!c&&e.filterable&&J(),p&&!O&&x&&(R.value=G),te(),c?ne([],[]):ne(null,null)}function qe(o){!Ee(o,"action")&&!Ee(o,"empty")&&!Ee(o,"header")&&o.preventDefault()}function Ge(o){h(o)}function Ne(o){var c,p,O,x,k;if(!e.keyboard){o.preventDefault();return}switch(o.key){case" ":if(e.filterable)break;o.preventDefault();case"Enter":if(!(!((c=$.value)===null||c===void 0)&&c.isComposing)){if(j.value){const P=(p=K.value)===null||p===void 0?void 0:p.getPendingTmNode();P?Oe(P):e.filterable||(J(),Ae())}else if(re(),e.tag&&de.value){const P=T.value[0];if(P){const W=P[e.valueField],{value:he}=C;e.multiple&&Array.isArray(he)&&he.includes(W)||oe(P)}}}o.preventDefault();break;case"ArrowUp":if(o.preventDefault(),e.loading)return;j.value&&((O=K.value)===null||O===void 0||O.prev());break;case"ArrowDown":if(o.preventDefault(),e.loading)return;j.value?(x=K.value)===null||x===void 0||x.next():re();break;case"Escape":j.value&&(gn(o),J()),(k=$.value)===null||k===void 0||k.focus();break}}function Ae(){var o;(o=$.value)===null||o===void 0||o.focus()}function De(){var o;(o=$.value)===null||o===void 0||o.focusInput()}function Je(){var o;j.value&&((o=q.value)===null||o===void 0||o.syncPosition())}D(),Fe(U(e,"options"),D);const Ye={focus:()=>{var o;(o=$.value)===null||o===void 0||o.focus()},focusInput:()=>{var o;(o=$.value)===null||o===void 0||o.focusInput()},blur:()=>{var o;(o=$.value)===null||o===void 0||o.blur()},blurInput:()=>{var o;(o=$.value)===null||o===void 0||o.blurInput()}},je=_(()=>{const{self:{menuBoxShadow:o}}=f.value;return{"--n-menu-box-shadow":o}}),me=u?Ue("select",void 0,je,e):void 0;return Object.assign(Object.assign({},Ye),{mergedStatus:ye,mergedClsPrefix:i,mergedBordered:t,namespace:d,treeMate:w,isMounted:kn(),triggerRef:$,menuRef:K,pattern:B,uncontrolledShow:E,mergedShow:j,adjustedTo:to(e),uncontrolledValue:s,mergedValue:C,followerRef:q,localizedPlaceholder:ae,selectedOption:ge,selectedOptions:fe,mergedSize:be,mergedDisabled:ce,focused:I,activeWithoutMenuOpen:de,inlineThemeDisabled:u,onTriggerInputFocus:xe,onTriggerInputBlur:ue,handleTriggerOrMenuResize:Je,handleMenuFocus:Se,handleMenuBlur:Be,handleMenuTabOut:_e,handleTriggerClick:Me,handleToggle:Oe,handleDeleteOption:oe,handlePatternInput:v,handleClear:H,handleTriggerBlur:Ie,handleTriggerFocus:ke,handleKeydown:Ne,handleMenuAfterLeave:pe,handleMenuClickOutside:$e,handleMenuScroll:Ge,handleMenuKeydown:Ne,handleMenuMousedown:qe,mergedTheme:f,cssVars:u?void 0:je,themeClass:me?.themeClass,onRender:me?.onRender})},render(){return a("div",{class:`${this.mergedClsPrefix}-select`},a(wn,null,{default:()=>[a(yn,null,{default:()=>a(jn,{ref:"triggerRef",inlineThemeDisabled:this.inlineThemeDisabled,status:this.mergedStatus,inputProps:this.inputProps,clsPrefix:this.mergedClsPrefix,showArrow:this.showArrow,maxTagCount:this.maxTagCount,ellipsisTagPopoverProps:this.ellipsisTagPopoverProps,bordered:this.mergedBordered,active:this.activeWithoutMenuOpen||this.mergedShow,pattern:this.pattern,placeholder:this.localizedPlaceholder,selectedOption:this.selectedOption,selectedOptions:this.selectedOptions,multiple:this.multiple,renderTag:this.renderTag,renderLabel:this.renderLabel,filterable:this.filterable,clearable:this.clearable,disabled:this.mergedDisabled,size:this.mergedSize,theme:this.mergedTheme.peers.InternalSelection,labelField:this.labelField,valueField:this.valueField,themeOverrides:this.mergedTheme.peerOverrides.InternalSelection,loading:this.loading,focused:this.focused,onClick:this.handleTriggerClick,onDeleteOption:this.handleDeleteOption,onPatternInput:this.handlePatternInput,onClear:this.handleClear,onBlur:this.handleTriggerBlur,onFocus:this.handleTriggerFocus,onKeydown:this.handleKeydown,onPatternBlur:this.onTriggerInputBlur,onPatternFocus:this.onTriggerInputFocus,onResize:this.handleTriggerOrMenuResize,ignoreComposition:this.ignoreComposition},{arrow:()=>{var e,i;return[(i=(e=this.$slots).arrow)===null||i===void 0?void 0:i.call(e)]}})}),a(xn,{ref:"followerRef",show:this.mergedShow,to:this.adjustedTo,teleportDisabled:this.adjustedTo===to.tdkey,containerClass:this.namespace,width:this.consistentMenuWidth?"target":void 0,minWidth:"target",placement:this.placement},{default:()=>a(po,{name:"fade-in-scale-up-transition",appear:this.isMounted,onAfterLeave:this.handleMenuAfterLeave},{default:()=>{var e,i,t;return this.mergedShow||this.displayDirective==="show"?((e=this.onRender)===null||e===void 0||e.call(this),Yo(a(In,Object.assign({},this.menuProps,{ref:"menuRef",onResize:this.handleTriggerOrMenuResize,inlineThemeDisabled:this.inlineThemeDisabled,virtualScroll:this.consistentMenuWidth&&this.virtualScroll,class:[`${this.mergedClsPrefix}-select-menu`,this.themeClass,(i=this.menuProps)===null||i===void 0?void 0:i.class],clsPrefix:this.mergedClsPrefix,focusable:!0,labelField:this.labelField,valueField:this.valueField,autoPending:!0,nodeProps:this.nodeProps,theme:this.mergedTheme.peers.InternalSelectMenu,themeOverrides:this.mergedTheme.peerOverrides.InternalSelectMenu,treeMate:this.treeMate,multiple:this.multiple,size:this.menuSize,renderOption:this.renderOption,renderLabel:this.renderLabel,value:this.mergedValue,style:[(t=this.menuProps)===null||t===void 0?void 0:t.style,this.cssVars],onToggle:this.handleToggle,onScroll:this.handleMenuScroll,onFocus:this.handleMenuFocus,onBlur:this.handleMenuBlur,onKeydown:this.handleMenuKeydown,onTabOut:this.handleMenuTabOut,onMousedown:this.handleMenuMousedown,show:this.mergedShow,showCheckmark:this.showCheckmark,resetMenuOnOptionsChange:this.resetMenuOnOptionsChange,scrollbarProps:this.scrollbarProps}),{empty:()=>{var d,u;return[(u=(d=this.$slots).empty)===null||u===void 0?void 0:u.call(d)]},header:()=>{var d,u;return[(u=(d=this.$slots).header)===null||u===void 0?void 0:u.call(d)]},action:()=>{var d,u;return[(u=(d=this.$slots).action)===null||u===void 0?void 0:u.call(d)]}}),this.displayDirective==="show"?[[Qo,this.mergedShow],[co,this.handleMenuClickOutside,void 0,{capture:!0}]]:[[co,this.handleMenuClickOutside,void 0,{capture:!0}]])):null}})})]}))}});export{zn as F,Yn as N,eo as a,In as b,Ln as c,ho as g,Ze as m};
